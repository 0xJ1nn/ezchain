// Generated from the Playwright-exported mark.svg. Do not edit the geometry.
const MARK_SVG = `<svg class="mark" viewBox="20 20 80 80" style="height:24px;width:24px" aria-hidden="true"><defs>
    <mask id="ezm3a" maskUnits="userSpaceOnUse" x="0" y="0" width="120" height="120"><rect width="120" height="120" fill="#fff"></rect><clipPath id="ezm3c72.65"><circle cx="60" cy="72.65" r="8.5"></circle></clipPath><path d="M66 46H86A14 14 0 0 1 86 74H66A14 14 0 0 1 66 46Z" fill="none" stroke="#000" stroke-width="15.2" clip-path="url(#ezm3c72.65)"></path></mask>
    <mask id="ezm3b" maskUnits="userSpaceOnUse" x="0" y="0" width="120" height="120"><rect width="120" height="120" fill="#fff"></rect><clipPath id="ezm3c47.35"><circle cx="60" cy="47.35" r="8.5"></circle></clipPath><path d="M34 46H54A14 14 0 0 1 54 74H34A14 14 0 0 1 34 46Z" fill="none" stroke="#000" stroke-width="15.2" clip-path="url(#ezm3c47.35)"></path></mask></defs>
    <g transform="rotate(-45 60 60)" fill="none" stroke="currentColor" stroke-width="10"><path d="M34 46H54A14 14 0 0 1 54 74H34A14 14 0 0 1 34 46Z" mask="url(#ezm3a)"></path><path d="M66 46H86A14 14 0 0 1 86 74H66A14 14 0 0 1 66 46Z" mask="url(#ezm3b)"></path></g></svg>`;

export function Mark({ size }: { size: number }) {
  const html = MARK_SVG.replace(
    "height:24px;width:24px",
    `height:${size}px;width:${size}px`,
  );
  return <span className="mark" dangerouslySetInnerHTML={{ __html: html }} />;
}
