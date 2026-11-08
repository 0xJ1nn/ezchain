import { PositionsSkeleton } from "@/components/route-states";
import { AppShell } from "@/components/shell";

export default function Loading() {
	return (
		<AppShell active="positions">
			<PositionsSkeleton />
		</AppShell>
	);
}
