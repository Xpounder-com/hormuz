export type TextBlock = {
  kind: 'paragraph' | 'heading' | 'code' | 'list';
  text: string;
  language?: string;
};
// Text-only Markdown subset. Never interprets HTML, loads media or executes code.
export function textBlocks(text: string): TextBlock[] {
  const blocks: TextBlock[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) {
      blocks.push({ kind: 'paragraph', text: paragraph.join('\n') });
      paragraph = [];
    }
  };
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith('```')) {
      flush();
      const language = line.slice(3).trim().slice(0, 30);
      const code: string[] = [];
      while (++i < lines.length && !lines[i].startsWith('```')) code.push(lines[i]);
      blocks.push({ kind: 'code', text: code.join('\n'), language });
    } else if (/^#{1,3}\s/.test(line)) {
      flush();
      blocks.push({ kind: 'heading', text: line.replace(/^#{1,3}\s/, '') });
    } else if (/^[-*]\s/.test(line)) {
      flush();
      blocks.push({ kind: 'list', text: line.slice(2) });
    } else if (!line.trim()) flush();
    else paragraph.push(line);
  }
  flush();
  return blocks;
}
