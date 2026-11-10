#!/usr/bin/env python3
"""Configure the public repository and refresh README download links."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
START = '<!-- release-links:start -->'
END = '<!-- release-links:end -->'


def repository_name(value):
    value = value.removeprefix('https://github.com/').rstrip('/').removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+', value):
        raise ValueError('请输入 GitHub 仓库地址或 OWNER/REPO。')
    if value.split('/')[1] in ('.', '..'):
        raise ValueError('仓库名称无效。')
    return value


def configure(repository, root=ROOT, maintainer=None):
    repository = repository_name(repository)
    path = root/'config/release.json'
    config = json.loads(path.read_text())
    config['repository'] = repository
    if maintainer:
        if '\n' in maintainer or '\r' in maintainer:
            raise ValueError('维护者信息必须为单行。')
        config['maintainer'] = maintainer
    readme = root/'README.md'
    source = readme.read_text()
    if source.count(START) != 1 or source.count(END) != 1:
        raise ValueError('README 下载区域标记缺失或重复。')
    url = 'https://github.com/' + repository
    links = (f'{START}\n'
             f'**[下载最新版 DEB（Jetson arm64）]({url}/releases/latest/download/cosmos-camera_latest_arm64.deb)**'
             f' · [SHA256 校验文件]({url}/releases/latest/download/cosmos-camera_latest_arm64.deb.sha256)'
             f' · [全部版本与更新说明]({url}/releases)\n\n'
             '下载链接在首次 GitHub Release 成功发布后生效。\n'
             f'{END}')
    source = source[:source.index(START)] + links + source[source.index(END)+len(END):]
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n')
    readme.write_text(source)
    return url


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', help='https://github.com/OWNER/REPO 或 OWNER/REPO')
    parser.add_argument('--maintainer', help='Name <email>')
    args = parser.parse_args()
    try:
        print('已更新 README 下载链接：' + configure(args.repository, maintainer=args.maintainer))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
