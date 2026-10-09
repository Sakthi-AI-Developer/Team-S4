const CLASSES = [
  ['Agriculture', '#d2a85e'],
  ['Vegetation', '#4b9c68'],
  ['Water', '#55a8db'],
  ['Built-up', '#d66f5e'],
  ['Bare land', '#b59576'],
];

const INDEX_LEGENDS = {
  rgb: { gradient: 'rgb-gradient', low: 'Dark', high: 'Bright' },
  ndvi: { gradient: 'ndvi-gradient', low: 'Very low', high: 'Very high' },
  ndwi: { gradient: 'ndwi-gradient', low: 'Low water signal', high: 'High water signal' },
  ndbi: { gradient: 'ndbi-gradient', low: 'Lower built-up indicator', high: 'Higher built-up indicator' },
  change_detection: { gradient: 'change-gradient', low: 'Decrease', high: 'Increase' },
};

export default function Legend({ layer }) {
  const key = layer === 'change' ? 'change_detection' : layer;
  if (key === 'landcover') {
    return (
      <div className="categorical-legend" aria-label="Land-cover legend">
        {CLASSES.map(([name, color]) => <span key={name}><i style={{ backgroundColor: color }} />{name}</span>)}
      </div>
    );
  }
  const item = INDEX_LEGENDS[key] ?? INDEX_LEGENDS.ndvi;
  return (
    <div className="continuous-legend" aria-label={`${key} map legend`}>
      <span>{item.low}</span><i className={item.gradient} /><span>{item.high}</span>
    </div>
  );
}
