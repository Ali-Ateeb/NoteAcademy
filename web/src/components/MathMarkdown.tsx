import "katex/dist/katex.min.css";

import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

/**
 * Model output: Markdown with LaTeX maths. Raw HTML in it is not rendered
 * (react-markdown escapes it), so this is safe to feed text a model wrote.
 *
 * remark-math only reads `$...$` and `$$...$$`; models sometimes write
 * `\(...\)` and `\[...\]` instead, so those are rewritten first.
 */
function normaliseMathDelimiters(text: string): string {
  return text
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, body: string) => `\n$$${body}$$\n`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, body: string) => `$${body}$`);
}

const components: Components = {
  h1: (props) => <h3 className="mt-4 text-base font-bold text-ink first:mt-0" {...props} />,
  h2: (props) => <h3 className="mt-4 text-base font-bold text-ink first:mt-0" {...props} />,
  h3: (props) => <h3 className="mt-4 text-base font-bold text-ink first:mt-0" {...props} />,
  h4: (props) => <h4 className="mt-3 text-sm font-bold text-ink first:mt-0" {...props} />,
  h5: (props) => <h4 className="mt-3 text-sm font-bold text-ink first:mt-0" {...props} />,
  h6: (props) => <h4 className="mt-3 text-sm font-bold text-ink first:mt-0" {...props} />,
  p: (props) => <p className="mt-2 first:mt-0" {...props} />,
  ul: (props) => <ul className="mt-2 list-disc space-y-1 pl-5" {...props} />,
  ol: (props) => <ol className="mt-2 list-decimal space-y-1 pl-5" {...props} />,
  strong: (props) => <strong className="font-bold text-ink" {...props} />,
  hr: () => <hr className="my-3 border-line" />,
  a: (props) => (
    <a className="text-accent underline" target="_blank" rel="noopener noreferrer" {...props} />
  ),
  blockquote: (props) => (
    <blockquote className="mt-2 border-l-2 border-accent/40 pl-3 text-ink-2" {...props} />
  ),
  code: (props) => <code className="rounded bg-surface px-1 font-mono text-[0.85em]" {...props} />,
  table: (props) => (
    <div className="mt-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-left" {...props} />
    </div>
  ),
  th: (props) => <th className="border border-line px-2 py-1 font-bold" {...props} />,
  td: (props) => <td className="border border-line px-2 py-1" {...props} />,
};

export function MathMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }]]}
      components={components}
    >
      {normaliseMathDelimiters(children)}
    </ReactMarkdown>
  );
}
