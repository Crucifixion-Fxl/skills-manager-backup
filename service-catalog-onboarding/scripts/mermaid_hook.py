# From service-catalog-onboarding skill scripts/. Copy to <repo>/mermaid_hook.py,
# referenced by mkdocs.yml as `hooks: [mermaid_hook.py]`. See SKILL.md §4 for context.
"""MkDocs hook：把 ```mermaid 围栏在渲染前替换成 <pre class="mermaid"><code>…</code></pre>。

为什么需要这个 hook：
- RHDH 自带的 mkdocs-techdocs-core 的 techdocs-core 插件**覆盖了 markdown_extensions**，没法在
  mkdocs.yml 里给 pymdownx.superfences 加一个 mermaid 自定义围栏 —— ```mermaid``` 块会被当成
  普通高亮代码块输出（带行号 / .highlighttable / <br/> 被转义），TechDocs Mermaid addon 不认。
- TechDocs Mermaid addon（backstage-plugin-techdocs-addon-mermaid）渲染 `.mermaid` 元素时会
  `el.querySelector("code")` 取里面 <code> 的 textContent 作为图源 —— 没有 <code> 子元素就直接跳过。
  所以必须是 `<pre class="mermaid"><code>…</code></pre>` 这个结构（外层 pre 带 mermaid class，
  内层 code 放图源）。
- 图源里的 `<` `>` `&` `"` 要 HTML 转义：MkDocs 把这段原始 HTML 原样透传，浏览器解析 <code> 内容时
  会把 `&lt;br/&gt;` 还原成字面字符串 `<br/>` 交给 mermaid.js（按 htmlLabels 渲染成节点内换行）；
  若不转义，浏览器会把 `<br/>` 当真的 BR 元素，textContent 取不到它 → 节点标签换行丢失。

节点换行：在 ```mermaid``` 里继续用 `<br/>`（不要用 `\n`）—— 经本 hook 转义后 mermaid 能正确换行。
"""
import html
import re

# 匹配独立成段的 ```mermaid 围栏（行首可有缩进；结尾 ``` 同样）
_FENCE = re.compile(r'(?ms)^[ \t]*```[ \t]*mermaid[ \t]*\n(.*?)\n[ \t]*```[ \t]*$')


def _replace(match):
    diagram = match.group(1)
    return '<pre class="mermaid"><code>' + html.escape(diagram) + '</code></pre>'


def on_page_markdown(markdown, page=None, config=None, files=None, **kwargs):
    return _FENCE.sub(_replace, markdown)
