# 题库补丁记录（D:\化学竞赛题库）

神奋 0.5.0 起，组卷页与题库页都能「真删题目」。这个能力需要题库那边多一个接口，
而题库是本机另一个独立项目（`D:\化学竞赛题库`，源码在 `chemy/`），所以这里记一笔改了什么、
以后题库自己更新时怎么补回来。

## 1. 新增删除接口

文件：`D:\化学竞赛题库\chemy\app.py`

```python
@app.delete("/api/admin/questions/{qid}")
def api_delete_question(request: Request, qid: str):
    require_admin(request)
    ...
```

行为：

1. 逐表清掉这道题的附属数据：`parts`、`scoring`、`answers`、`estimates`、`figures`、`questions_fts`；
2. 删 `questions` 里的主记录；
3. 如果这道题来自「导入批次」（`source.paperId` 以 `import-` 开头），且这张卷子已经一道题都不剩，
   连带删掉 `paper_sources` / `papers`，并把对应批次标成 `rolled_back`；
4. 返回 `{"ok": true, "deleted": qid, "title": ...}`。

注意：`db.get_question()` 返回的卷子 id 在 `q["source"]["paperId"]`，
不是顶层 `paperId`（第一版就写错了这里，导致删完留下空卷）。

## 2. 题库自己的页面加了删除按钮

文件：`D:\化学竞赛题库\chemy\web\app.js`

详情页工具条里插入「删除这道题」（红色描边），点了先 `confirm()`，
确认后 `fetch('/api/admin/questions/{id}', { method: 'DELETE' })`，
成功后从当前列表里移除并提示「已删除」。

## 3. 改完怎么让它生效

`chemy/` 源码目录的优先级高于 exe 里打包的代码，所以只需要重启题库：

```bat
netstat -ano | findstr :8080     :: 找到 PID
taskkill /F /PID <PID>
start "" D:\化学竞赛题库\chemy.exe
```

神奋这边不用动，`/api/chemy/delete` 会自动用上新接口。

## 4. 以后题库要更新怎么办

题库更新会覆盖 `chemy/app.py` 与 `chemy/web/app.js`，上面两处改动会丢。
更新后按本文档重新贴一遍即可（代码不长），然后重启题库，并在神奋里
「组卷 → 多选 → 从题库删除」点一次确认，看到题库统计减少就说明补丁回来了。

## 5. 安全护栏

- 真删之前，神奋会要求二次确认（前端 `confirm` + 后端要求 `confirm == "删除"`）。
- 建议每月在题库页点一次「备份」，删除后要找回只能靠备份。
- 删除只影响本地题库的 `chemy.db`，不会动 `D:\初赛`、`D:\决赛` 里的原始 PDF。
