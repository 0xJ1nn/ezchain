import { PerformanceSkeleton } from "@/components/route-states";
import { AppShell } from "@/components/shell";

export default function Loading() {
	return (
		<AppShell active="performance">
			<PerformanceSkeleton />
		</AppShell>
	);
}
