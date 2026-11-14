// OrbitNexus NASA Flight Surgeon Clinical Fixtures & Aerospace Standards
export const vitals = [
  {
    id: 'heart',
    name: 'Resting Heart Rate',
    value: '72.0',
    unit: 'BPM',
    icon: 'heart',
    note: 'Nominal (Target 60–85 BPM)',
    source: 'Optical Dual-Wavelength PPG @ 125 Hz',
    description: 'Continuous ventricular chronotropic frequency inferred from pulsatile radial photoplethysmography. In microgravity, cardiac stroke work decreases by ~15% due to reduced lower-body hydrostatic blood column pressure.',
    points: '0,23 12,21 20,23 27,8 34,29 41,15 48,21 60,21 69,18 80,21 90,16 100,20'
  },
  {
    id: 'oxygen',
    name: 'Arterial Oxygen Saturation (SpO2)',
    value: '98.6',
    unit: '%',
    icon: 'droplet',
    note: 'Sea-level PO2 Equivalent',
    source: 'Transcutaneous Pulse Oximeter',
    description: 'Fraction of oxygen-saturated hemoglobin relative to total hemoglobin. Critical for detecting insidious hypobaric hypoxia in pressurized space vehicle cabins and extravehicular mobility units (EMU).',
    points: '0,20 12,21 24,19 35,21 47,16 60,18 73,14 84,17 100,14'
  },
  {
    id: 'temperature',
    name: 'Core Body Temperature',
    value: '36.85',
    unit: '°C',
    icon: 'temperature',
    note: 'Temporal Heat-Flux Normal',
    source: 'Double-Sensor Pre-Auricular Heat Flux',
    description: 'Deep core thermal equilibrium measured via dual-sensor heat flux thermometry. In microgravity, natural convective cooling is abolished, predisposing astronauts to spaceflight-associated exercise hyperthermia.',
    points: '0,23 13,20 22,22 34,18 43,19 55,16 68,18 80,15 90,18 100,15'
  },
  {
    id: 'water',
    name: 'Total Body Water / Hydration Index',
    value: '62.4',
    unit: '% TBW',
    icon: 'droplet',
    note: 'Cephalad Fluid Shift -1.8L',
    source: 'Multi-Frequency Bioimpedance (BIS)',
    description: 'Continuous bioimpedance spectroscopy measuring extracellular and intracellular fluid volumes. Accurately quantifies microgravity cephalad fluid redistribution, plasma volume contraction, and dehydration trajectory.',
    points: '0,22 15,22 30,21 45,20 60,21 75,21 90,20 100,21'
  },
  {
    id: 'pressure',
    name: 'Continuous Arterial Blood Pressure',
    value: '118/76',
    unit: 'mmHg',
    icon: 'pulse',
    note: 'MAP: 90 mmHg (Normotensive)',
    source: 'Tonometric Non-Invasive Continuous BP',
    description: 'Aortic pressure waveform reconstruction. Eliminates gravity-dependent hydrostatic pressure gradient between head and feet, preventing lower-extremity vascular pooling and altering carotid baroreflex sensitivity.',
    points: '0,18 15,16 30,22 45,15 60,20 75,17 90,19 100,18'
  },
  {
    id: 'respiration',
    name: 'Ventilatory Respiration Rate',
    value: '16.2',
    unit: 'brpm',
    icon: 'activity',
    note: 'Eupneic (Target 12–20 brpm)',
    source: 'Transthoracic Impedance Pneumography',
    description: 'Transthoracic impedance plethysmography tracking respiratory frequency. Monitors alveolar ventilation-perfusion matching and provides early warning for localized carbon dioxide (CO2) pocket inhalation.',
    points: '0,21 16,14 32,24 48,15 64,22 80,16 100,20'
  },
  {
    id: 'hrv',
    name: 'Autonomic HRV (RMSSD)',
    value: '48.5',
    unit: 'ms',
    icon: 'pulse',
    note: 'Sympathovagal Balance: 1.15',
    source: 'ECG/PPG Inter-Beat Interval (IBI)',
    description: 'Root Mean Square of Successive RR interval Differences. Clinical gold standard for assessing parasympathetic vagal tone, high cognitive workload, allostatic overload, and physical exertion recovery.',
    points: '0,24 20,18 40,25 60,16 80,22 100,18'
  }
];

