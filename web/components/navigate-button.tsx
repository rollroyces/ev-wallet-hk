/**
 * Web "Navigate" button — opens Google Maps in a new tab with turn-by-turn
 * directions to the given coordinates.
 *
 * Server component compatible — pure `<a href>` with no JS.
 * For mobile, see /mobile/lib/navigation.ts + StationCard.tsx which use
 * the platform-native maps URL scheme.
 */

export interface NavigateButtonProps {
  latitude: number;
  longitude: number;
  name?: string;
}

export function NavigateButton({ latitude, longitude, name }: NavigateButtonProps): React.JSX.Element {
  const params = new URLSearchParams({
    api: '1',
    destination: `${latitude},${longitude}`,
    travelmode: 'driving',
  });
  if (name) {
    // destination_place_id is informational; Google Maps geocodes the coords
    // regardless. We include the name as a hint.
    params.set('destination_place_id', name);
  }
  const href = `https://www.google.com/maps/dir/?${params.toString()}`;

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="btn btn-primary"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.5rem',
        padding: '0.6rem 1rem',
        background: '#0f172a',
        color: '#fff',
        borderRadius: '0.5rem',
        textDecoration: 'none',
        fontWeight: 600,
        fontSize: '0.95rem',
      }}
    >
      <span aria-hidden>🗺️</span>
      <span>Navigate</span>
    </a>
  );
}