import { StrategiesSkeleton } from "@/components/route-states";
import { AppShell } from "@/components/shell";

export default function Loading() {
	return (
		<AppShell active="strategies">
			<StrategiesSkeleton />
		</AppShell>
	);
}
