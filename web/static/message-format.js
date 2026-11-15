/* Small, text-only Markdown subset. No raw HTML, links or embedded media. */
(() => {
  function node(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function inline(root, text) {
    const pattern = /\*\*([^*\n]+)\*\*|`([^`\n]+)`|\*([^*\n]+)\*/g;
    let previous = 0, match;
    while ((match = pattern.exec(text))) {
      if (match.index > previous) root.appendChild(node('span', text.slice(previous, match.index)));
      root.appendChild(node(match[1] ? 'strong' : match[2] ? 'code' : 'em', match[1] || match[2] || match[3]));
      previous = pattern.lastIndex;
    }
    if (previous < text.length) root.appendChild(node('span', text.slice(previous)));
  }
  function render(root, value) {
    root.replaceChildren();
    const lines = String(value).replace(/\r\n?/g, '\n').split('\n');
    let paragraph = [], list = null, fenced = false, code = [];
    function flush() {
      if (paragraph.length) {
        const text = paragraph.join('\n');
        let blocks = [text];
        // Older saved replies can contain very long paragraphs. Break them at
        // sentence boundaries for reading, without regenerating or shortening.
        if (text.length > 650 && typeof Intl.Segmenter === 'function') {
          const sentences = [...new Intl.Segmenter('en', {granularity:'sentence'}).segment(text)].map(item => item.segment);
          blocks = [];
          for (let i = 0; i < sentences.length; i += 2) blocks.push(sentences.slice(i, i + 2).join(''));
        }
        for (const block of blocks) { const p = node('p'); inline(p, block); root.appendChild(p); }
        paragraph = [];
      }
      list = null;
    }
    for (const line of lines) {
      if (/^\s*```/.test(line)) {
        flush();
        if (fenced) { const pre = node('pre'); pre.appendChild(node('code', code.join('\n'))); root.appendChild(pre); code = []; }
        fenced = !fenced; continue;
      }
      if (fenced) { code.push(line); continue; }
      if (!line.trim()) { flush(); continue; }
      const heading = line.match(/^\s*#{1,6}\s+(.+)$/) || line.match(/^\s*\*\*([^*]+)\*\*\s*$/);
      if (heading) { flush(); const h = node('h4'); inline(h, heading[1]); root.appendChild(h); continue; }
      const item = line.match(/^\s*(?:([-*])\s+|(\d+)[.)]\s+)(.+)$/);
      if (item) {
        if (paragraph.length) flush();
        const type = item[2] ? 'ol' : 'ul';
        if (!list || list.tagName.toLowerCase() !== type) {
          list = node(type); if (item[2]) list.start = Number(item[2]); root.appendChild(list);
        }
        const li = node('li'); inline(li, item[3]); list.appendChild(li); continue;
      }
      if (/^>\s?/.test(line)) { flush(); const quote = node('blockquote'); inline(quote, line.replace(/^>\s?/, '')); root.appendChild(quote); continue; }
      list = null; paragraph.push(line);
    }
    flush();
    if (fenced && code.length) { const pre = node('pre'); pre.appendChild(node('code', code.join('\n'))); root.appendChild(pre); }
  }
  if (typeof module !== 'undefined') module.exports = {render};
  if (typeof window !== 'undefined') window.DebateMessage = {render};
})();
