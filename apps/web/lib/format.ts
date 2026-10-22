// Shared display formatters for fixture view models. Money is already computed by the
// server view model; these functions only format it. Unknown values are passed as null
// by the caller and must never become zero.

export const MINUS = "\u2212";

export function usd(n: number) {
	return `${n < 0 ? MINUS : ""}$${Math.abs(n).toLocaleString("en-US", {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	})}`;
}

export function signedUsd(n: number) {
	return `${n >= 0 ? "+" : MINUS}$${Math.abs(n).toLocaleString("en-US", {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	})}`;
}

export function usd0(n: number) {
	return `${n < 0 ? MINUS : ""}$${Math.abs(n).toLocaleString("en-US", {
		maximumFractionDigits: 0,
	})}`;
}

export function pct(n: number) {
	return `${n >= 0 ? "+" : MINUS}${Math.abs(n).toFixed(1)}%`;
}

export function signedNumber(n: number, digits = 2) {
	return `${n >= 0 ? "+" : MINUS}${Math.abs(n).toFixed(digits)}`;
}
