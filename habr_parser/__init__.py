"""habr-corpus-parser — сбор статей с habr.com в markdown-корпус.

Ядро построено на stdlib (urllib) + markdownify для HTML→markdown.
Зависимости httpx/selectolax/rich/PyYAML из SPEC добавляются по мере
надобности их функций (HTML-фолбэк и т.п.).
"""

__version__ = "0.1.0"
