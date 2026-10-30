# 参与贡献

先说结论：**任何规模的贡献都欢迎**——改错别字、补文档、提 Bug、加功能都算。

## 你可以怎么帮上忙

| 类型 | 怎么做 |
| --- | --- |
| 报告 Bug | 开 Issue，附：系统版本、`python --version`、`node -v`、复现步骤、预期 vs 实际、截图（界面问题请截整窗） |
| 提功能 | 开 Issue 说清**教学场景**（你是什么课、哪一步最费时间），比只说「加个按钮」有用得多 |
| 改代码 | Fork → 新建分支 → 提交 → 开 PR，一个 PR 只做一件事 |
| 补文档 | README / `docs/` 的措辞、截图、教程都缺人 |

## 本地开发

```bash
# 1) 后端
python -m venv .venv
.venv/Scripts/activate                  # macOS/Linux: source .venv/bin/activate
pip install -r apps/api/requirements.txt

# 2) 前端
cd apps/web && npm install && npm run build && cd ../..

# 3) 起服务
python -m uvicorn app.main:app --app-dir apps/api --reload --port 8100
# 前端热更新：另开终端 cd apps/web && npm run dev

# 4) 测试（用独立临时库，不碰 data/tiku.db）
python -m pytest apps/api/tests -q
```

前端 dev server 通过 Vite 代理把 `/api` 转发到 8100，具体见 `apps/web/vite.config.js`。

## 代码约定

- **数据库改动**一律写进 `apps/api/app/db.py` 的 `_migrate()`，用 `PRAGMA table_info` 判断后 `ALTER TABLE`，
  保证老用户升级时不用删库。
- **新增接口**请同时在 README 的「主要接口」表里加一行。
- **测试**：新功能带一个最小用例；夹具放 `apps/api/tests/fixtures/`，不要依赖用户的真实数据。
- **前端**：不引入新的 UI 框架；样式优先复用 `apps/web/src/style.css` 里的类（`.card` / `.btnrow` / `.chip` 等）。
  公式一律用 `apps/web/src/math.js` 的 `renderMath()` 渲染，不要自己写一套。
- **后端**：路由薄、服务厚——能复用的逻辑放 `apps/api/app/services/`。
- 提交信息用 `feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:` 前缀。

## 不要提交的东西

- 任何 API Key、token、`.env`
- `data/` 下的数据库、题图、PDF 原件（`.gitignore` 已覆盖，提 PR 前请 `git status` 自查）
- 教材 / 文献的扫描件（版权问题，仓库永远不放）

## 许可

提交 PR 即表示你同意以本仓库的 [MIT 许可证](LICENSE) 授权你的贡献。
本项目基于 [cy-0754/tiku-studio](https://github.com/cy-0754/tiku-studio) 二次开发，衍生时请保留原版权声明。
