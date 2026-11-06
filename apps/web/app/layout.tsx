import type { Metadata } from "next";
import localFont from "next/font/local";
import "@ezchain/ui/tokens.css";
import "./ui.css";

const inter = localFont({
	src: "./fonts/inter-latin-var.woff2",
	variable: "--font-inter",
	display: "swap",
	weight: "100 900",
});

const plexMono = localFont({
	src: [
		{ path: "./fonts/ibm-plex-mono-400.woff2", weight: "400", style: "normal" },
		{ path: "./fonts/ibm-plex-mono-500.woff2", weight: "500", style: "normal" },
		{ path: "./fonts/ibm-plex-mono-600.woff2", weight: "600", style: "normal" },
	],
	variable: "--font-plex-mono",
	display: "swap",
});

export const metadata: Metadata = {
	title: "ezCHAIN — trading workstation",
	description:
		"Open-source, multi-chain crypto trading workstation. Paper first, your keys, your strategies.",
};

// Dark is primary (design/BRIEF.md). The inline bootstrap sets html[data-theme]
// before first paint so the persisted or system-guessed skin never flashes.
// It reads the stored choice; with no stored choice it guesses from
// prefers-color-scheme (light only when the system prefers light), else dark.
const THEME_BOOTSTRAP = `try{var t=localStorage.getItem("ezchain.theme");if(t!=="dark"&&t!=="light"){t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="dark"}`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
	return (
		<html
			lang="en"
			className={`${inter.variable} ${plexMono.variable}`}
			data-theme="dark"
			suppressHydrationWarning
		>
			{/* The inline sidebar bootstrap sets body.sidebar-collapsed before paint so the
			    persisted rail never flashes. React did not render that class, so suppress the
			    expected attribute difference instead of warning and regenerating the tree. */}
			<body suppressHydrationWarning>
				<script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
				{children}
			</body>
		</html>
	);
}
