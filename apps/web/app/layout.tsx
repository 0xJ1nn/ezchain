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

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
	return (
		<html lang="en" className={`${inter.variable} ${plexMono.variable}`}>
			{/* The inline sidebar bootstrap sets body.sidebar-collapsed before paint so the
			    persisted rail never flashes. React did not render that class, so suppress the
			    expected attribute difference instead of warning and regenerating the tree. */}
			<body suppressHydrationWarning>{children}</body>
		</html>
	);
}
