export function EquityChart() {
	return (
		<div className="chart">
			<svg viewBox="0 0 900 220" role="img" aria-labelledby="equity-chart-title equity-chart-desc">
				<title id="equity-chart-title">Equity curve, last week, paper mode</title>
				<desc id="equity-chart-desc">
					Equity $3,482.16, +4.8% over 24 hours. Max drawdown −6.2% from a peak of
					$3,712. Cost basis is the dashed line.
				</desc>
				<line className="gridline" x1="0" y1="30" x2="860" y2="30" />
				<line className="gridline" x1="0" y1="80" x2="860" y2="80" />
				<line className="gridline" x1="0" y1="130" x2="860" y2="130" />
				<line className="gridline" x1="0" y1="180" x2="860" y2="180" />
				<line className="axis" x1="860" y1="0" x2="860" y2="210" />
				<text className="axis-text" x="866" y="33">
					$3,700
				</text>
				<text className="axis-text" x="866" y="83">
					$3,500
				</text>
				<text className="axis-text" x="866" y="133">
					$3,300
				</text>
				<text className="axis-text" x="866" y="183">
					$3,100
				</text>
				<line className="level-stop" x1="0" y1="84" x2="860" y2="84" />
				<text className="axis-text" x="782" y="78">
					−6.2% DD
				</text>
				<polyline
					className="series-2"
					points="0,150 80,146 160,152 240,140 320,144 400,132 480,136 560,124 640,128 720,118 800,120 860,116"
				/>
				<polyline
					className="series"
					points="0,168 40,160 80,166 120,150 160,158 200,144 240,150 280,138 320,146 360,128 400,136 440,122 480,130 520,112 560,120 600,104 640,116 680,96 720,108 760,92 800,100 840,88 860,84"
				/>
				<circle className="dot" cx="860" cy="84" r="3" />
				<line className="crosshair" x1="600" y1="0" x2="600" y2="210" />
				<circle className="dot" cx="600" cy="104" r="3" />
				<rect x="610" y="60" width="128" height="52" fill="var(--surface)" stroke="var(--line-2)" />
				<text className="axis-text" x="618" y="76">
					Sat 14:00
				</text>
				<text className="num-label" x="618" y="92">
					$3,404.00
				</text>
				<text className="axis-text" x="618" y="106">
					+8.1% / 7d
				</text>
				<text className="axis-text" x="2" y="212">
					Oct 13
				</text>
				<text className="axis-text" x="420" y="212">
					Oct 17
				</text>
				<text className="axis-text" x="800" y="212">
					Oct 20
				</text>
			</svg>
		</div>
	);
}
