// Ionicons are exported unchanged from the brand board into public/icons/ion.svg.
// The sprite is referenced by fragment, never re-drawn here.
export function Ion({
	name,
	size = 20,
	className,
}: {
	name: string;
	size?: number;
	className?: string;
}) {
	return (
		<svg
			className={className ? `ion ${className}` : "ion"}
			width={size}
			height={size}
			aria-hidden="true"
		>
			<use href={`/icons/ion.svg#${name}`} />
		</svg>
	);
}
