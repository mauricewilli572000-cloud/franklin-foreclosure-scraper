// The list of dashboards, shared by the landing page and every dashboard's navigation bar.
// To move a tab to a different dashboard, change its page's `keys` in that page's HTML file.
// To show the probate dashboard here, put its address in `url` (leave it '' to hide it).
window.DASHBOARDS = [
  { id: 'foreclosures', label: 'Foreclosures', url: 'foreclosures.html',
    blurb: 'E-Foreclosure filings from the county Clerks of Courts, matched to parcel owners, with interest rate and amount owed.',
    tabs: 'Franklin, Cuyahoga, Summit' },
  { id: 'code-enforcement', label: 'Code enforcement', url: 'code-enforcement.html',
    blurb: 'Open code-violation cases on distressed properties, matched to owners from Auditor records.',
    tabs: 'Columbus, Cleveland' },
  { id: 'excess-funds', label: 'Excess funds', url: 'excess-funds.html',
    blurb: 'Surplus money left over from foreclosure and tax sales that owners can claim.',
    tabs: 'Franklin, United States' },
  { id: 'evictions', label: 'Evictions', url: 'evictions.html',
    blurb: 'Landlords with repeated eviction filings, with the real owner of each rental.',
    tabs: 'Franklin County' },
  { id: 'agents', label: 'Agents', url: 'agents.html',
    blurb: 'Agents who sell distressed listings, with direct phone numbers, for off-market deals.',
    tabs: 'Franklin, Cuyahoga, Summit, Hamilton' },
  { id: 'probate', label: 'Probate', url: '',
    blurb: 'Probate and estate leads.',
    tabs: '' },
];
