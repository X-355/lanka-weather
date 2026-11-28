// Shared "live" data published by GitHub Actions every 3 hours (see lankaweather/snapshots.py).
// Returns the parsed file if it exists and is fresh enough, else null so the caller can ask the service directly.
export async function snapshot(name, maxAgeHours = 6) {
  try {
    const r = await fetch(`data/live/${name}.json`, { cache: "no-cache" });
    if (!r.ok) return null;
    const j = await r.json();
    const age = (Date.now() - Date.parse(j.generated)) / 3600e3;
    return age >= 0 && age <= maxAgeHours ? j : null;
  } catch (e) {
    return null;
  }
}
