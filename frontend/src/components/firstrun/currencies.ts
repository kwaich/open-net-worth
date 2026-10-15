// The base-currency picker's options, built from the runtime's own ICU data rather than a
// hand-kept array.
//
// WHY this exists: the outgoing wizard offered 14 hardcoded codes on the one step with no
// skip — so a user in Switzerland, Korea, Brazil, the UAE, New Zealand or Taiwan could not
// pick their own base currency at all. Base currency is load-bearing under the historical-rate
// lock: a stored `base_amount` is trusted only while its `base_currency` matches the current
// base, so a wrong choice here mis-stamps every row written before it is fixed.
//
// The same 14-item array was duplicated in Settings' CurrencySection; both read this now.

// Used only when `Intl.supportedValuesOf` is unavailable (Safari < 15.4, some CI images).
const FALLBACK_CODES = [
  'AED',
  'ARS',
  'AUD',
  'BRL',
  'CAD',
  'CHF',
  'CLP',
  'CNY',
  'COP',
  'CZK',
  'DKK',
  'EGP',
  'EUR',
  'GBP',
  'HKD',
  'HUF',
  'IDR',
  'ILS',
  'INR',
  'JPY',
  'KRW',
  'MXN',
  'MYR',
  'NGN',
  'NOK',
  'NZD',
  'PHP',
  'PKR',
  'PLN',
  'RON',
  'SAR',
  'SEK',
  'SGD',
  'THB',
  'TRY',
  'TWD',
  'UAH',
  'USD',
  'VND',
  'ZAR',
]

// Region → its currency. Not every ISO region: enough to cover the places a self-hoster is
// realistically in, with USD as the fallback. Only ever a DEFAULT — the picker lists all 162.
const REGION_CURRENCY: Record<string, string> = {
  AE: 'AED',
  AR: 'ARS',
  AT: 'EUR',
  AU: 'AUD',
  BE: 'EUR',
  BD: 'BDT',
  BG: 'BGN',
  BH: 'BHD',
  BR: 'BRL',
  CA: 'CAD',
  CH: 'CHF',
  CL: 'CLP',
  CN: 'CNY',
  CO: 'COP',
  CY: 'EUR',
  CZ: 'CZK',
  DE: 'EUR',
  DK: 'DKK',
  EE: 'EUR',
  EG: 'EGP',
  ES: 'EUR',
  FI: 'EUR',
  FR: 'EUR',
  GB: 'GBP',
  GR: 'EUR',
  HK: 'HKD',
  HR: 'EUR',
  HU: 'HUF',
  ID: 'IDR',
  IE: 'EUR',
  IL: 'ILS',
  IN: 'INR',
  IS: 'ISK',
  IT: 'EUR',
  JP: 'JPY',
  KE: 'KES',
  KR: 'KRW',
  KW: 'KWD',
  LK: 'LKR',
  LT: 'EUR',
  LU: 'EUR',
  LV: 'EUR',
  MA: 'MAD',
  MT: 'EUR',
  MX: 'MXN',
  MY: 'MYR',
  NG: 'NGN',
  NL: 'EUR',
  NO: 'NOK',
  NZ: 'NZD',
  PE: 'PEN',
  PH: 'PHP',
  PK: 'PKR',
  PL: 'PLN',
  PT: 'EUR',
  QA: 'QAR',
  RO: 'RON',
  RS: 'RSD',
  SA: 'SAR',
  SE: 'SEK',
  SG: 'SGD',
  SI: 'EUR',
  SK: 'EUR',
  TH: 'THB',
  TR: 'TRY',
  TW: 'TWD',
  UA: 'UAH',
  US: 'USD',
  VN: 'VND',
  ZA: 'ZAR',
}

function allCodes(): string[] {
  try {
    const supported = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf
    const all = supported?.('currency')
    if (all && all.length) return all
  } catch {
    // Fall through to the static list.
  }
  return FALLBACK_CODES
}

function nameFor(code: string): string {
  try {
    return new Intl.DisplayNames(undefined, { type: 'currency' }).of(code) || code
  } catch {
    return code
  }
}

export interface CurrencyOption {
  value: string
  label: string
}

export const CURRENCY_OPTIONS: CurrencyOption[] = allCodes().map((code) => ({
  value: code,
  label: `${code} — ${nameFor(code)}`,
}))

const KNOWN = new Set(CURRENCY_OPTIONS.map((o) => o.value))

/**
 * The currency this machine's locale implies — the prefilled default on first run, so the
 * step is a one-click confirmation rather than a hunt through a list of 162.
 *
 * Never throws and always returns a code in CURRENCY_OPTIONS; falls back to USD.
 */
// IANA timezone -> ISO country. Timezone is set from WHERE THE MACHINE IS; the locale is set
// from what language its owner reads. Someone in Singapore running en-US — the common case —
// was silently handed USD by locale alone. Only zones whose country this app has a currency
// for are listed; anything else falls through to the locale guess below.
const ZONE_REGION: Record<string, string> = {
  'Asia/Singapore': 'SG',
  'Asia/Hong_Kong': 'HK',
  'Asia/Tokyo': 'JP',
  'Asia/Seoul': 'KR',
  'Asia/Shanghai': 'CN',
  'Asia/Chongqing': 'CN',
  'Asia/Urumqi': 'CN',
  'Asia/Taipei': 'TW',
  'Asia/Bangkok': 'TH',
  'Asia/Jakarta': 'ID',
  'Asia/Makassar': 'ID',
  'Asia/Jayapura': 'ID',
  'Asia/Pontianak': 'ID',
  'Asia/Kuala_Lumpur': 'MY',
  'Asia/Kuching': 'MY',
  'Asia/Manila': 'PH',
  'Asia/Ho_Chi_Minh': 'VN',
  'Asia/Saigon': 'VN',
  'Asia/Phnom_Penh': 'KH',
  'Asia/Vientiane': 'LA',
  'Asia/Yangon': 'MM',
  'Asia/Dhaka': 'BD',
  'Asia/Colombo': 'LK',
  'Asia/Kathmandu': 'NP',
  'Asia/Karachi': 'PK',
  'Asia/Kolkata': 'IN',
  'Asia/Calcutta': 'IN',
  'Asia/Dubai': 'AE',
  'Asia/Riyadh': 'SA',
  'Asia/Qatar': 'QA',
  'Asia/Bahrain': 'BH',
  'Asia/Kuwait': 'KW',
  'Asia/Muscat': 'OM',
  'Asia/Amman': 'JO',
  'Asia/Beirut': 'LB',
  'Asia/Jerusalem': 'IL',
  'Asia/Tel_Aviv': 'IL',
  'Asia/Baghdad': 'IQ',
  'Asia/Tehran': 'IR',
  'Asia/Almaty': 'KZ',
  'Asia/Tashkent': 'UZ',
  'Asia/Baku': 'AZ',
  'Asia/Tbilisi': 'GE',
  'Asia/Yerevan': 'AM',
  'Europe/London': 'GB',
  'Europe/Dublin': 'IE',
  'Europe/Lisbon': 'PT',
  'Europe/Madrid': 'ES',
  'Europe/Paris': 'FR',
  'Europe/Brussels': 'BE',
  'Europe/Amsterdam': 'NL',
  'Europe/Luxembourg': 'LU',
  'Europe/Berlin': 'DE',
  'Europe/Zurich': 'CH',
  'Europe/Vienna': 'AT',
  'Europe/Rome': 'IT',
  'Europe/Malta': 'MT',
  'Europe/Athens': 'GR',
  'Europe/Nicosia': 'CY',
  'Europe/Copenhagen': 'DK',
  'Europe/Oslo': 'NO',
  'Europe/Stockholm': 'SE',
  'Europe/Helsinki': 'FI',
  'Atlantic/Reykjavik': 'IS',
  'Europe/Tallinn': 'EE',
  'Europe/Riga': 'LV',
  'Europe/Vilnius': 'LT',
  'Europe/Warsaw': 'PL',
  'Europe/Prague': 'CZ',
  'Europe/Bratislava': 'SK',
  'Europe/Budapest': 'HU',
  'Europe/Ljubljana': 'SI',
  'Europe/Zagreb': 'HR',
  'Europe/Belgrade': 'RS',
  'Europe/Sofia': 'BG',
  'Europe/Bucharest': 'RO',
  'Europe/Kyiv': 'UA',
  'Europe/Kiev': 'UA',
  'Europe/Moscow': 'RU',
  'Europe/Istanbul': 'TR',
  'Africa/Cairo': 'EG',
  'Africa/Casablanca': 'MA',
  'Africa/Lagos': 'NG',
  'Africa/Nairobi': 'KE',
  'Africa/Accra': 'GH',
  'Africa/Johannesburg': 'ZA',
  'America/New_York': 'US',
  'America/Detroit': 'US',
  'America/Chicago': 'US',
  'America/Denver': 'US',
  'America/Phoenix': 'US',
  'America/Los_Angeles': 'US',
  'America/Anchorage': 'US',
  'Pacific/Honolulu': 'US',
  'America/Toronto': 'CA',
  'America/Vancouver': 'CA',
  'America/Edmonton': 'CA',
  'America/Winnipeg': 'CA',
  'America/Halifax': 'CA',
  'America/Mexico_City': 'MX',
  'America/Bogota': 'CO',
  'America/Lima': 'PE',
  'America/Santiago': 'CL',
  'America/Argentina/Buenos_Aires': 'AR',
  'America/Sao_Paulo': 'BR',
  'America/Montevideo': 'UY',
  'Australia/Sydney': 'AU',
  'Australia/Melbourne': 'AU',
  'Australia/Brisbane': 'AU',
  'Australia/Perth': 'AU',
  'Australia/Adelaide': 'AU',
  'Pacific/Auckland': 'NZ',
}

/** The currency this machine most likely thinks in.

    Timezone first, locale second. Both are guesses and the screen shows the answer with a
    Change beside it — the point is that the guess is right far more often for the many people
    whose device language is not their country's. */
export function defaultCurrencyFromLocale(): string {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    const region = zone ? ZONE_REGION[zone] : undefined
    const byZone = region ? REGION_CURRENCY[region] : undefined
    if (byZone && KNOWN.has(byZone)) return byZone
  } catch {
    // An environment without a resolvable timezone still gets the locale guess below.
  }
  try {
    const locale = new Intl.NumberFormat().resolvedOptions().locale
    const region = new Intl.Locale(locale).region
    const guess = region ? REGION_CURRENCY[region.toUpperCase()] : undefined
    if (guess && KNOWN.has(guess)) return guess
  } catch {
    // An exotic or unavailable locale is not an error — USD is a defensible default.
  }
  return 'USD'
}
