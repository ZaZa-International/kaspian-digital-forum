/** Shared model position; interpolates only from a recorded departure, never invented GPS. */
function knownDeparture(detail, legId, observedAt) {
  const events = detail?.events || [];
  const replaced = new Set(
    events.map((event) => event.correction_of).filter(Boolean),
  );
  const departures = events.filter(
    (event) =>
      event.leg_id === legId &&
      event.event_type === "departure" &&
      event.source !== "demo-simulation" &&
      !replaced.has(event.id),
  );
  return departures.reduce((latest, event) => {
    const time = Date.parse(
      event.actual_ts ||
        event.occurred_ts ||
        (!event.planned_ts && event.received_ts),
    );
    return time <= observedAt && time > latest ? time : latest;
  }, -Infinity);
}
export function calculateRouteProgress(detail, plan = detail?.plan, projection = detail?.baseline, horizonHours = 0, isCurrentPlan = plan === detail?.plan) {
  const legs = plan?.legs || [];
  if (!legs.length) return 0;
  const completed = new Set(plan.completed_leg_ids || []);
  const timings = new Map((projection?.legs || []).map((leg) => [leg.id, leg]));
  const shipment = detail?.shipment;
  const observedAt = Date.parse(detail?.horizon?.as_of);
  const departure = isCurrentPlan
    ? knownDeparture(detail, shipment?.current_leg_id, observedAt)
    : NaN;
  let horizon = Math.max(0, Number(horizonHours) || 0);
  for (let index = 0; index < legs.length; index++) {
    const leg = legs[index];
    if (completed.has(leg.id)) continue;
    const timing = timings.get(leg.id);
    if (timing?.remaining_h == null) return index / legs.length;
    const remaining = Math.max(0, timing.remaining_h);
    const waiting = timing.in_transit
      ? 0
      : Math.min(
          remaining,
          (timing.wait_h || 0) + (timing.weather?.wave_m >= 2 ? 48 : 0),
        );
    if (horizon < remaining || (horizon === 0 && timing.in_transit)) {
      const elapsed =
        timing.in_transit &&
        Number.isFinite(departure) &&
        Number.isFinite(observedAt)
          ? Math.max(0, (observedAt - departure) / 3600000)
          : 0;
      const moving = Math.max(0, horizon - waiting) + elapsed;
      const duration = remaining - waiting + elapsed;
      const fraction = duration > 0 ? Math.min(moving / duration, 1) : 0;
      return (index + fraction) / legs.length;
    }
    horizon -= remaining;
  }
  return 1;
}
