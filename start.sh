#!/bin/bash
# Shot2MD 启动脚本
# 依赖: python-tk@3.9, pillow, pyperclip, openai
#
# 首次安装:
#   brew install python-tk@3.9
#   /opt/homebrew/opt/python@3.9/bin/pip3.9 install pillow pyperclip openai

cd "$(dirname "$0")"
/opt/homebrew/opt/python@3.9/bin/python3.9 shot2md.py
