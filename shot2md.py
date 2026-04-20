#!/usr/bin/env python3
"""
Shot2MD - Screenshot to Markdown
接管 Cmd+Option+Shift+4 截图 → AI OCR → 剪贴板
"""

import os
import sys
import base64
import subprocess
import threading
import time
import configparser
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox
import PIL.ImageGrab
import PIL.Image
import pyperclip

try:
    from openai import OpenAI
except ImportError:
    print("请安装 openai: pip3 install openai")
    sys.exit(1)

# ========== 配置路径 ==========
APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "config.ini"
PROMPT_FILE = APP_DIR / "prompt.md"

DEFAULT_PROMPT = """## 角色：
你是一位顶级的 Markdown 文档工程师和技术写作排版专家，精通文档解析、手写识别、图像分析和结构化信息抽取。

## 目标：
将用户提供的截图转换成结构清晰、语义准确的 Markdown 文档。

## 输出要求：
直接输出 Markdown 内容，包含：
- 标题层级 (#)
- 数学公式用 $...$ 或 $$...$$
- 代码块标注语言
- 表格用 GFM 语法
- 列表用 - 或 1.
- 保留所有原文内容和格式

不要在代码块外添加任何说明"""


# ========== macOS 通知 ==========
def notify(title: str, message: str):
    subprocess.run([
        'osascript', '-e',
        f'display notification "{message}" with title "{title}"'
    ], capture_output=True)


# ========== macOS Keychain 存储 API Key ==========
class Keychain:
    SERVICE = "shot2md"

    @staticmethod
    def save(key: str):
        subprocess.run(
            ['security', 'add-generic-password', '-a', 'api_key', '-s', Keychain.SERVICE, '-w', key, '-U'],
            capture_output=True
        )

    @staticmethod
    def load() -> str:
        result = subprocess.run(
            ['security', 'find-generic-password', '-a', 'api_key', '-s', Keychain.SERVICE, '-w'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""

    @staticmethod
    def delete():
        subprocess.run(
            ['security', 'delete-generic-password', '-a', 'api_key', '-s', Keychain.SERVICE],
            capture_output=True
        )


# ========== 配置管理 ==========
class Config:
    def __init__(self):
        self.api_key = ""
        self.base_url = ""
        self.model_name = "gemini-2.5-pro"
        self.load()

    def load(self):
        # API Key 从 Keychain 读取
        self.api_key = Keychain.load()
        # 其他配置从文件读取
        if CONFIG_FILE.exists():
            cfg = configparser.ConfigParser()
            cfg.read(CONFIG_FILE)
            self.base_url = cfg.get("api", "base_url", fallback="")
            self.model_name = cfg.get("api", "model", fallback=self.model_name)

    def save(self, api_key: str, base_url: str, model: str):
        # API Key 存到 Keychain
        Keychain.save(api_key)
        # 其他配置存到文件
        cfg = configparser.ConfigParser()
        cfg["api"] = {
            "base_url": base_url,
            "model": model
        }
        with open(CONFIG_FILE, "w") as f:
            cfg.write(f)
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model


# ========== OCR 核心 ==========
class OCREngine:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def _load_prompt(self) -> str:
        if PROMPT_FILE.exists():
            return PROMPT_FILE.read_text(encoding="utf-8")
        return DEFAULT_PROMPT

    def transcribe(self, image_paths: list) -> str:
        prompt = self._load_prompt()
        content = [{"type": "text", "text": prompt}]

        for path in image_paths:
            with open(path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_data}"}
            })

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            max_tokens=16384,
            temperature=0.1
        )
        return response.choices[0].message.content


# ========== 剪贴板图片监听 ==========
class ClipboardWatcher:
    def __init__(self, on_image_ready):
        self.on_image_ready = on_image_ready
        self.running = False
        self.last_hash = None

    def _get_clipboard_image(self):
        try:
            img = PIL.ImageGrab.grabclipboard()
            if img is None:
                return None
            if isinstance(img, list):
                for f in img:
                    if str(f).lower().endswith(('.png', '.jpg', '.jpeg', '.tiff')):
                        return f
                return None
            if isinstance(img, PIL.Image.Image):
                return img
            return None
        except Exception:
            return None

    def _save_image(self, img):
        tmp = APP_DIR / f"capture_{int(time.time() * 1000)}.png"
        if isinstance(img, (Path, str)):
            return str(img)
        img.save(str(tmp), "PNG")
        return str(tmp)

    def start(self):
        self.running = True

        def watch():
            while self.running:
                img = self._get_clipboard_image()
                if img is not None:
                    img_hash = None
                    if isinstance(img, PIL.Image.Image):
                        img_hash = hash(img.tobytes())
                    elif isinstance(img, (str, Path)):
                        img_hash = str(img)

                    if img_hash != self.last_hash:
                        self.last_hash = img_hash
                        path = self._save_image(img)
                        if path:
                            self.on_image_ready(path)

                time.sleep(0.3)

        threading.Thread(target=watch, daemon=True).start()

    def stop(self):
        self.running = False


# ========== 设置窗口 ==========
class SettingsWindow:
    def __init__(self, parent, config: Config, on_save):
        self.config = config
        self.on_save = on_save
        self.dialog = tk.Toplevel(parent)
        self._setup_ui()

    def _setup_ui(self):
        self.dialog.title("Shot2MD 设置")
        self.dialog.geometry("500x300")
        self.dialog.resizable(False, False)
        self.dialog.transient()
        self.dialog.grab_set()

        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - 500) // 2
        y = (self.dialog.winfo_screenheight() - 300) // 2
        self.dialog.geometry(f"500x300+{x}+{y}")

        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(main_frame, text="API 地址 (OpenAI 兼容):", font=("", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 5)
        )
        self.base_url_var = tk.StringVar(value=self.config.base_url)
        ttk.Entry(main_frame, textvariable=self.base_url_var, width=50).grid(
            row=1, column=0, sticky="ew", pady=(0, 15)
        )

        ttk.Label(main_frame, text="API Key:", font=("", 10, "bold")).grid(
            row=2, column=0, sticky="w", pady=(0, 5)
        )
        self.api_key_var = tk.StringVar(value=self.config.api_key)
        ttk.Entry(
            main_frame,
            textvariable=self.api_key_var,
            show="*",
            width=50
        ).grid(row=3, column=0, sticky="ew", pady=(0, 15))

        ttk.Label(main_frame, text="模型:", font=("", 10, "bold")).grid(
            row=4, column=0, sticky="w", pady=(0, 5)
        )
        self.model_var = tk.StringVar(value=self.config.model_name)
        model_combo = ttk.Combobox(
            main_frame,
            textvariable=self.model_var,
            values=[
                "gemini-2.5-pro",
                "gemini-2.0-flash",
                "gpt-4o",
                "gpt-4o-mini",
                "claude-3-5-sonnet",
                "claude-3-5-haiku",
            ],
            width=47,
            state="readonly"
        )
        model_combo.grid(row=5, column=0, sticky="ew", pady=(0, 20))

        ttk.Label(
            main_frame,
            text="API Key 安全存储于 macOS Keychain 中",
            font=("", 9),
            foreground="gray"
        ).grid(row=6, column=0, sticky="w", pady=(0, 15))

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=7, column=0, sticky="e")

        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy).pack(
            side="right", padx=(10, 0)
        )
        ttk.Button(btn_frame, text="保存", command=self._save).pack(side="right")

    def _save(self):
        api_key = self.api_key_var.get().strip()
        base_url = self.base_url_var.get().strip()
        model = self.model_var.get().strip()

        if not api_key:
            messagebox.showwarning("提示", "请输入 API Key")
            return
        if not base_url:
            messagebox.showwarning("提示", "请输入 API 地址")
            return

        self.config.save(api_key, base_url, model)
        self.on_save()
        self.dialog.destroy()


# ========== 主应用 ==========
class Shot2MDApp:
    def __init__(self):
        self.config = Config()
        self.watcher = None

        self.root = tk.Tk()
        self.root.title("Shot2MD")
        self.root.geometry("300x220")
        self.root.resizable(False, False)
        self._setup_ui()

        self.watcher = ClipboardWatcher(self._on_new_screenshot)
        self.watcher.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_ui(self):
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(
            main_frame,
            text="Shot2MD",
            font=("SF Pro Display", 22, "bold")
        ).pack(pady=(5, 3))

        ttk.Label(
            main_frame,
            text="截图 → OCR → 剪贴板",
            font=("", 10),
            foreground="gray"
        ).pack(pady=(0, 12))

        self.status_var = tk.StringVar(value="⚠ 请先配置 API")
        if self.config.api_key:
            self.status_var.set(f"✓ 就绪 | {self.config.model_name}")

        ttk.Label(
            main_frame,
            textvariable=self.status_var,
            font=("", 10)
        ).pack(pady=(0, 12))

        usage = (
            "按 Cmd+Option+Shift+4 截图\n"
            "程序自动识别并转为 Markdown\n"
            "结果自动复制到剪贴板，Cmd+V 粘贴"
        )
        ttk.Label(
            main_frame,
            text=usage,
            font=("", 9),
            foreground="#666",
            justify="center"
        ).pack(pady=(0, 15))

        ttk.Button(
            main_frame,
            text="⚙️ 设置",
            command=self._open_settings
        ).pack()

    def _open_settings(self):
        SettingsWindow(self.root, self.config, self._on_config_save)

    def _on_config_save(self):
        self.status_var.set(f"✓ 就绪 | {self.config.model_name}")

    def _on_new_screenshot(self, image_path: str):
        self.root.after(0, lambda: self.status_var.set("🔄 识别中..."))
        notify("Shot2MD", "识别中...")
        self._process_image(image_path)

    def _process_image(self, image_path: str):
        if not image_path:
            return

        def do_ocr():
            try:
                engine = OCREngine(
                    self.config.api_key,
                    self.config.base_url,
                    self.config.model_name
                )
                result = engine.transcribe([image_path])

                pyperclip.copy(result)

                notify("Shot2MD", "已复制到剪贴板！Cmd+V 粘贴")
                self.root.after(0, lambda: self.status_var.set(
                    f"✓ 就绪 | {self.config.model_name}"
                ))

            except Exception as e:
                notify("Shot2MD", f"识别失败: {e}")
                self.root.after(0, lambda: messagebox.showerror("错误", f"识别失败: {e}"))
            finally:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except:
                    pass

        threading.Thread(target=do_ocr, daemon=True).start()

    def _on_close(self):
        if self.watcher:
            self.watcher.stop()
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = Shot2MDApp()
    app.run()
