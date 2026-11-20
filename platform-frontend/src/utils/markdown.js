/**
 * Markdown + KaTeX 数学公式 + DOMPurify XSS 防护统一渲染工具。
 * 支持块级公式 ($$...$$ / \[...\]) 与行内公式 ($...$ / \(...\))，适用于高校理工科课程 AI 助教解答渲染。
 */
import DOMPurify from 'dompurify'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { marked } from 'marked'

marked.setOptions({
  gfm: true,
  breaks: true,
})

function renderKatexSafe(tex, displayMode) {
  try {
    return katex.renderToString(tex.trim(), {
      displayMode,
      throwOnError: false,
      strict: false,
    })
  } catch {
    return tex
  }
}

export function renderMarkdownWithMath(rawText) {
  if (!rawText) return ''
  const mathTokens = []
  const stash = (tex, displayMode) => {
    const idx = mathTokens.length
    mathTokens.push(renderKatexSafe(tex, displayMode))
    return `@@KATEX_BLOCK_${idx}@@`
  }

  // 保护代码块，避免代码里的 $ 被误当公式
  const codeTokens = []
  let text = String(rawText).replace(/```[\s\S]*?```|`[^`\n]+`/g, (match) => {
    const idx = codeTokens.length
    codeTokens.push(match)
    return `@@CODE_STASH_${idx}@@`
  })

  // 1. 块级公式 $$...$$ 与 \[...\]
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => stash(tex, true))
  text = text.replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => stash(tex, true))
  // 2. 行内公式 $...$ 与 \(...\)
  text = text.replace(/(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)/g, (_, tex) => stash(tex, false))
  text = text.replace(/\\\((.+?)\\\)/g, (_, tex) => stash(tex, false))

  // 恢复代码块后交由 marked 转 HTML
  text = text.replace(/@@CODE_STASH_(\d+)@@/g, (_, idx) => codeTokens[Number(idx)] ?? '')
  let html = marked.parse(text)

  // 恢复 KaTeX HTML 并用 DOMPurify 清理潜在 XSS
  html = html.replace(/@@KATEX_BLOCK_(\d+)@@/g, (_, idx) => mathTokens[Number(idx)] ?? '')
  return DOMPurify.sanitize(html, {
    ADD_TAGS: ['math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'mfrac', 'msqrt', 'annotation'],
    ADD_ATTR: ['style', 'aria-hidden', 'encoding', 'xmlns'],
  })
}
