import type { Metadata } from "next";
import localFont from "next/font/local";
import "@ezchain/ui/tokens.css";
import "./ui.css";
import { HydrationMarker } from "./hydration-marker";

const sora = localFont({
	src: "./fonts/sora-latin-var.woff2",
	variable: "--font-sora",
	display: "swap",
	weight: "300 800",
});

const unbounded = localFont({
	src: "./fonts/unbounded-latin-var.woff2",
	variable: "--font-unbounded",
	display: "swap",
	weight: "400 800",
});

const jetbrainsMono = localFont({
	src: "./fonts/jetbrains-mono-latin-var.woff2",
	variable: "--font-jetbrains-mono",
	display: "swap",
	weight: "400 700",
});

export const metadata: Metadata = {
	title: "ezCHAIN — trading workstation",
	description:
		"Open-source, multi-chain crypto trading workstation. Paper first, your keys, your strategies.",
};

// Dark is the brand default. The inline bootstrap sets html[data-theme]
// before first paint so the persisted or system-guessed skin never flashes.
// It reads the stored choice; with no stored choice it guesses from
// prefers-color-scheme (light only when the system prefers light), else dark.
const THEME_BOOTSTRAP = `try{var t=localStorage.getItem("ezchain.theme");if(t!=="dark"&&t!=="light"){t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="dark"}`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
	return (
		<html
			lang="en"
			className={`${sora.variable} ${unbounded.variable} ${jetbrainsMono.variable}`}
			data-theme="dark"
			suppressHydrationWarning
		>
			{/* The inline sidebar bootstrap sets body.sidebar-collapsed before paint so the
			    persisted rail never flashes. React did not render that class, so suppress the
			    expected attribute difference instead of warning and regenerating the tree. */}
			<body suppressHydrationWarning>
				<script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
				<HydrationMarker />
				{children}
			</body>
		</html>
	);
}
