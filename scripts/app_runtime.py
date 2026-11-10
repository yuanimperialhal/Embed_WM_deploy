"""Paths and settings shared by source checkouts and installed desktop releases."""
import json
import os
import re
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP_ID = 'cosmos-camera'
VERSION = (ROOT/'VERSION').read_text(encoding='utf-8').strip()
DEPLOYMENT_PATH = Path('/var/lib/cosmos-camera/deployment.json')
DEFAULT_PROMPT = '用一句中文描述主要物体及位置。画面模糊就说明无法判断，不推断运动。'


def installed():
    return (ROOT/'INSTALL_MODE').is_file()


def user_base(variable, fallback):
    value = os.environ.get(variable, '')
    # XDG specifies that relative overrides must be ignored.
    return Path(value) if value and Path(value).is_absolute() else Path.home()/fallback


def settings_path():
    return user_base('XDG_CONFIG_HOME', '.config')/APP_ID/'settings.json'


def logs_dir():
    if not installed():
        return ROOT/'logs'
    return user_base('XDG_STATE_HOME', '.local/state')/APP_ID/'logs'


def cache_dir():
    if not installed():
        return ROOT/'cache'
    return user_base('XDG_CACHE_HOME', '.cache')/APP_ID


def default_settings():
    model = (user_base('XDG_DATA_HOME', '.local/share')/APP_ID/'models/cosmos-download'
             if installed() else ROOT/'models/cosmos-download')
    deployment = read_deployment()
    if deployment:
        model = Path(deployment['model_dir'])
    return dict(schema_version=1, model_dir=str(model), camera_source='/dev/video0',
                prompt=DEFAULT_PROMPT)


def read_deployment():
    """System defaults written by the offline installer; user settings still win."""
    if not installed():
        return {}
    try:
        data = json.loads(DEPLOYMENT_PATH.read_text(encoding='utf-8'))
        if (data.get('schema_version') != 1 or not isinstance(data.get('model_dir'), str)
                or not Path(data['model_dir']).is_absolute()
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', data.get('image_id', ''))):
            return {}
        return data
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def deployed_image(default):
    # docker save/load preserves image IDs; registry digest aliases may be absent.
    return read_deployment().get('image_id', default)


def validate_settings(settings):
    if not isinstance(settings, dict) or settings.get('schema_version') != 1:
        raise ValueError('配置格式不受支持，请保留原文件并检查软件版本。')
    result = default_settings()
    for key in ('model_dir', 'camera_source', 'prompt'):
        value = settings.get(key, result[key])
        if not isinstance(value, str) or not value.strip() or '\x00' in value:
            raise ValueError(f'配置项 {key} 不能为空，且必须为文字。')
        result[key] = value.strip()
    model = Path(result['model_dir']).expanduser()
    if not model.is_absolute() or ':' in str(model) or '\n' in str(model):
        raise ValueError('模型目录必须为绝对路径，且不能包含冒号或换行。')
    result['model_dir'] = str(model)
    if not 1 <= len(result['prompt']) <= 400:
        raise ValueError('问题应为 1–400 个字符。')
    return result


def load_settings():
    path = settings_path()
    if not path.exists():
        return default_settings()
    try:
        return validate_settings(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        raise ValueError(f'无法读取配置 {path}：{exc}') from exc


def save_settings(settings):
    settings = validate_settings(settings)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace atomically so an interrupted write leaves the previous file intact.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.settings-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return settings


def model_files_error(model_dir):
    required = ('model.safetensors', 'config.json', 'tokenizer.json', 'tokenizer_config.json',
                'preprocessor_config.json')
    missing = [name for name in required if not (Path(model_dir)/name).is_file()]
    if missing:
        return '模型目录缺少：' + '、'.join(missing) + '。请在“设置”中选择完整的 Cosmos 模型目录。'
    return None
