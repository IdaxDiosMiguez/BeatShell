//! Ecualizador gráfico de 10 bandas (presets) y filtros del modo DJ.

pub const BANDS: [u32; 10] = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];

/// Ganancias en dB por banda.
pub const PRESETS: &[(&str, [f64; 10])] = &[
    ("Plano", [0., 0., 0., 0., 0., 0., 0., 0., 0., 0.]),
    ("Graves+", [6., 5., 4., 2., 0., 0., 0., 0., 0., 0.]),
    ("Agudos+", [0., 0., 0., 0., 0., 1., 2., 4., 5., 6.]),
    ("Rock", [5., 4., 3., 1., -1., -1., 1., 3., 4., 5.]),
    ("Pop", [-1., 1., 3., 4., 3., 0., -1., -1., 1., 2.]),
    ("Electrónica", [6., 5., 2., 0., -2., 0., 1., 3., 4., 5.]),
    ("Hip-hop", [6., 5., 3., 1., -1., -1., 1., 1., 2., 3.]),
    ("Reggaetón", [6., 6., 3., 0., -1., 0., 1., 2., 3., 3.]),
    ("Jazz", [3., 2., 1., 2., -1., -1., 0., 1., 2., 3.]),
    ("Clásica", [4., 3., 2., 1., -1., -1., 0., 2., 3., 4.]),
    ("Acústica", [3., 3., 2., 1., 1., 1., 2., 3., 2., 1.]),
    ("Voces", [-2., -2., -1., 1., 3., 4., 4., 3., 1., 0.]),
    ("Noche", [5., 4., 2., 0., 0., 0., 0., 1., 3., 4.]),
];
pub const DEFAULT: &str = "Plano";

/// Modo DJ: nivela el volumen entre canciones para que ninguna suene más fuerte.
pub const DJ_LEVELER: &str = "dynaudnorm=f=300:g=15:p=0.9:m=10";

pub fn gains(name: &str) -> [f64; 10] {
    PRESETS
        .iter()
        .find(|(n, _)| *n == name)
        .map(|(_, g)| *g)
        .unwrap_or(PRESETS[0].1)
}

pub fn next_preset(name: &str) -> &'static str {
    let i = PRESETS.iter().position(|(n, _)| *n == name).unwrap_or(0);
    PRESETS[(i + 1) % PRESETS.len()].0
}

/// Formatea como Python: `6` → "6", `6.5` → "6.5".
fn num(x: f64) -> String {
    if x.fract() == 0.0 {
        format!("{}", x as i64)
    } else {
        format!("{x}")
    }
}

/// Cadena `-af` de ffmpeg para el preset (y el nivelado del modo DJ).
pub fn filter_chain(preset: &str, dj: bool) -> String {
    let mut parts = Vec::new();
    let g = gains(preset);
    let boost = g.iter().cloned().fold(0.0_f64, f64::max);
    if g.iter().any(|&x| x != 0.0) {
        if boost > 0.0 {
            // bajamos antes de realzar para no saturar
            parts.push(format!("volume=-{:.1}dB", boost * 0.7));
        }
        for (freq, gain) in BANDS.iter().zip(g) {
            if gain != 0.0 {
                parts.push(format!("equalizer=f={freq}:t=o:w=1:g={}", num(gain)));
            }
        }
    }
    if dj {
        parts.push(DJ_LEVELER.to_string());
    }
    parts.join(",")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_chain_as_python() {
        assert_eq!(filter_chain("Plano", false), "");
        assert_eq!(filter_chain("Plano", true), DJ_LEVELER);
        assert_eq!(
            filter_chain("Graves+", false),
            "volume=-4.2dB,equalizer=f=31:t=o:w=1:g=6,equalizer=f=62:t=o:w=1:g=5,\
             equalizer=f=125:t=o:w=1:g=4,equalizer=f=250:t=o:w=1:g=2"
        );
        assert!(filter_chain("Pop", false).contains("g=-1"));
    }
}
