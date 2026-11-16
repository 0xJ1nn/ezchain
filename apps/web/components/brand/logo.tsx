import { Mark } from "./mark";

// The mark is exported from the brand board (mark.svg); this component only
// assembles the lockup and sizes the mark as the board's EZ_LOGO does.
export function Logo({
	size = 17,
	mono = false,
	sub = false,
	className,
}: {
	size?: number;
	mono?: boolean;
	sub?: boolean;
	className?: string;
}) {
	return (
		<span
			className={["logo", mono && "mono", sub && "sub", className].filter(Boolean).join(" ")}
			style={{ fontSize: `${size}px` }}
			aria-label="ezCHAIN"
		>
			<span className="mk">
				<Mark size={Math.round(size * 1.15)} />
				<i>ez</i>
			</span>
			<b>CHAIN</b>
		</span>
	);
}
