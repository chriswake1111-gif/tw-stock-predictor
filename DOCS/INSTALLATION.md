# Installation

一般執行環境必須套用 repository 內的相容性 constraints：

```text
python -m pip install -c constraints.txt -r requirements.txt
```

開發與測試環境使用 `requirements-dev.txt`；該檔案會套用相同的 `constraints.txt`，避免本機與 CI 解析出不同的 FastAPI、Starlette、Pydantic、httpx2 或 pytest 版本。
