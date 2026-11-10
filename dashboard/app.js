
const COLUMNS = [
  { key: 'score', label: 'Score', sortable: true },
  { key: 'owner', label: 'Owner', sortable: true },
  { key: 'prop_address', label: 'Property', sortable: false },
  { key: 'amount', label: 'Amount owed', sortable: true },
  { key: 'interest_rate', label: 'Rate', sortable: true },
  { key: 'filed', label: 'Filed', sortable: true },
  { key: 'captured_date', label: 'Added', sortable: true },
  { key: 'mail_address', label: 'Mailing address', sortable: false },
  { key: 'flags', label: 'Flags', sortable: false },
];

const ALL_COUNTIES = [
  {
    key: 'franklin',
    label: 'Franklin County',
    dataUrl: 'records.json',
    tagline: "E-Foreclosure filings pulled nightly from the Clerk of Courts, matched against Auditor parcel records, and scored for outreach.",
    csvUrl: 'ghl_export.csv',
    // Franklin's records.json is already in the shape render()/applyFilters() expect.
    adapt(data) {
      return {
        fetched_at: data.fetched_at,
        total: data.total,
        with_address: data.with_address,
        new_this_run: data.new_this_run || 0,
        records: data.records || [],
      };
    },
  },
  {
    key: 'cuyahoga',
    label: 'Cuyahoga County',
    dataUrl: 'cuyahoga.json',
    tagline: "Foreclosure filings pulled nightly from the Cuyahoga County Clerk of Courts docket, enriched from the Fiscal Officer's parcel data, and scored for outreach.",
    csvUrl: 'cuyahoga_ghl_export.csv',
    // Cuyahoga's run_pipeline.py writes a different shape (LeadRecord
    // dicts under "leads") - map each one into the same row shape
    // Franklin's records use, so the rest of this page (render, sort,
    // filter, detail view) needs no county-specific logic at all.
    adapt(data) {
      const records = (data.leads || []).map(l => ({
        doc_num: l.case_number,
        case_number: l.case_number,
        doc_type: l.lead_type || 'Foreclosure Complaint',
        filed: l.filing_date,
        owner: l.defendant || l.owner_name,
        grantee: l.plaintiff,
        amount: l.amount_owed,
        legal: '',
        prop_address: l.property_address,
        prop_city: l.property_city,
        prop_state: l.property_state,
        prop_zip: l.property_zip,
        mail_address: l.mailing_address,
        mail_city: '',
        mail_state: '',
        mail_zip: '',
        clerk_url: l.source_url,
        flags: [],
        score: l.lead_score,
        interest_rate: l.interest_rate,
        loan_type: l.loan_type,
      }));
      return {
        fetched_at: data.generated_at,
        total: data.total_leads || 0,
        with_address: data.leads_with_mailing_address || 0,
        new_this_run: 0,
        records,
      };
    },
  },
  {
    key: 'summit',
    label: 'Summit County',
    dataUrl: 'https://mauricewilli572000-cloud.github.io/summit-county-leads/summit.json',
    tagline: "New foreclosure filings from the Summit County Clerk's live new-filings feed (usually within a day or two of filing), matched to parcel records where possible, and scored for outreach.",
    csvUrl: 'https://mauricewilli572000-cloud.github.io/summit-county-leads/summit_ghl_export.csv',
    // Summit lives in its own repo (summit-county-leads) and publishes summit.json on its own GitHub Pages site;
    // this tab reads that file by URL. Same LeadRecord shape as Cuyahoga's, plus flags and added_date.
    adapt(data) {
      const records = (data.leads || []).map(l => ({
        doc_num: l.case_number,
        case_number: l.case_number,
        doc_type: l.lead_type || 'Foreclosure',
        filed: l.filing_date,
        captured_date: l.added_date,
        owner: l.owner_name || l.defendant,
        grantee: l.plaintiff,
        amount: l.amount_owed,
        legal: '',
        prop_address: l.property_address,
        prop_city: l.property_city,
        prop_state: l.property_state,
        prop_zip: l.property_zip,
        mail_address: l.mailing_address,
        mail_city: '',
        mail_state: '',
        mail_zip: '',
        clerk_url: l.source_url,
        flags: l.flags || [],
        score: l.lead_score,
        interest_rate: l.interest_rate,
        loan_type: l.loan_type,
      }));
      return {
        fetched_at: data.generated_at,
        total: data.total_leads || 0,
        with_address: data.leads_with_property_address || 0,
        new_this_run: 0,
        records,
      };
    },
  },
  {
    key: 'evictions',
    label: 'Franklin Evictions',
    dataUrl: 'https://mauricewilli572000-cloud.github.io/franklin-eviction-leads/evictions.json',
    tagline: "Tired-landlord leads: landlords who keep filing evictions in Franklin County Municipal Court (Clerk's monthly F.E.D. reports, last 13 months), grouped by landlord and scored for outreach. Verify ownership on the Auditor site before contacting.",
    csvUrl: 'https://mauricewilli572000-cloud.github.io/franklin-eviction-leads/evictions_ghl_export.csv',
    // Lives in its own repo (franklin-eviction-leads) and publishes evictions.json on its own GitHub Pages site.
    // One record = one LANDLORD (plaintiff), not one case.
    columns: [
      { key: 'score', label: 'Score', sortable: true },
      { key: 'owner', label: 'Landlord', sortable: true },
      { key: 'filings_12m', label: 'Filings (12 mo)', sortable: true },
      { key: 'property_count', label: 'Rentals', sortable: true },
      { key: 'filed', label: 'Last filing', sortable: true },
      { key: 'captured_date', label: 'Added', sortable: true },
      { key: 'mail_address', label: 'Mailing address', sortable: false },
      { key: 'flags', label: 'Flags', sortable: false },
    ],
    adapt(data) {
      const typeLabel = { individual: 'Individual', llc: 'LLC', company: 'Company', trust: 'Trust/estate',
                          manager: 'Manager/agent - verify owner', institutional: 'Institutional' };
      const records = (data.records || []).map(l => {
        const flags = [typeLabel[l.owner_type] || l.owner_type];
        if (l.property_kind === 'apartment complex') flags.push('Apartment complex');
        if (l.mailing_state && l.mailing_state !== 'OH') flags.push('Out-of-state owner');
        if (l.filings_90d >= 3) flags.push('Active last 90 days');
        const verif = { 'verified': 'Owner verified (Auditor)', 'via manager': 'Owner found via manager',
                        'plaintiff not owner of record': 'Plaintiff is not owner of record',
                        'sold since filing': 'Rentals sold since filing (may have exited)' }[l.verification];
        if (verif) flags.push(verif);
        if (l.sold_since > 0 && l.verification !== 'sold since filing') flags.push(l.sold_since + ' rental(s) sold since filing');
        return {
          doc_num: l.lead_id,
          case_number: (l.case_numbers || []).join(', '),
          doc_type: l.lead_type,
          filed: l.last_filing,
          captured_date: l.date_added,
          owner: l.owner_name,
          grantee: '',
          prop_address: (l.property_addresses || []).join('; '),
          mail_address: l.mailing_street,
          mail_city: l.mailing_city,
          mail_state: l.mailing_state,
          mail_zip: l.mailing_zip,
          flags,
          score: l.score,
          filings_12m: l.filings_12m,
          property_count: l.property_count,
          raw: l,
        };
      });
      return {
        fetched_at: data.generated_at,
        total: data.total_leads || 0,
        with_address: data.leads_with_mailing_address || 0,
        new_this_run: data.new_this_run || 0,
        records,
      };
    },
    rowHTML(r, flagsHTML) {
      const x = r.raw;
      return `
        <td><span class="score ${scoreClass(r.score)}">${r.score}</span></td>
        <td>${esc(r.owner) || '—'}</td>
        <td>${x.filings_12m}<br><span class="muted">${x.filings_90d} in last 90 days</span></td>
        <td>${x.property_count}</td>
        <td>${esc(r.filed) || '—'}</td>
        <td>${esc(r.captured_date) || '—'}</td>
        <td>${esc(fullMailAddress(r)) || '<span class="muted">not on file</span>'}</td>
        <td>${flagsHTML}</td>`;
    },
    detailHTML(r) {
      const x = r.raw;
      const cases = x.case_numbers || [];
      const rentals = (x.property_addresses || []);
      return `
        <dl class="detail-grid">
          <div><dt>Owner type</dt><dd>${esc(r.flags[0])}</dd></div>
          <div><dt>Property profile</dt><dd>${esc(x.property_kind) || '—'}</dd></div>
          <div><dt>Filings (window / 12 mo / 90 days)</dt><dd>${x.filings_window} / ${x.filings_12m} / ${x.filings_90d}</dd></div>
          <div><dt>First / last filing</dt><dd>${esc(x.first_filing) || '—'} / ${esc(x.last_filing) || '—'}</dd></div>
          <div><dt>Dismissed / judgments / writs</dt><dd>${x.dismissed} / ${x.judgments} / ${x.writs}</dd></div>
          <div><dt>Distinct rental addresses</dt><dd>${x.property_count}</dd></div>
          <div><dt>Owner verification (Auditor parcels)</dt><dd>${esc(x.verification) || '—'}</dd></div>
          <div><dt>Rentals matched to a parcel</dt><dd>${x.parcels_matched || 0} of ${x.property_count}</dd></div>
          <div><dt>Rentals sold since the filing</dt><dd>${x.sold_since || 0}</dd></div>
          <div><dt>Owner's parcels countywide</dt><dd>${x.owner_portfolio ? x.owner_portfolio : '—'}</dd></div>
          <div class="full"><dt>Why it scored ${r.score}</dt><dd>${esc((x.signals || []).join(' · '))}</dd></div>
          <div class="full"><dt>${(x.verification === 'verified' || x.verification === 'via manager') ? 'Owner mailing address (Auditor)' : 'Mailing address (court filing)'}</dt><dd>${esc(fullMailAddress(r)) || '—'}</dd></div>
          ${x.court_mailing ? `<div class="full"><dt>Mailing address on the court filing</dt><dd>${esc(x.court_mailing)}</dd></div>` : ''}
          ${(x.via_managers || []).length ? `<div class="full"><dt>Filed through manager(s)</dt><dd>${esc((x.via_managers || []).join(' · '))}</dd></div>` : ''}
          <div class="full"><dt>Rental addresses (defendant addresses${rentals.length < x.property_count ? '; first ' + rentals.length + ' shown' : ''})</dt><dd>${esc(rentals.join(' · ')) || '—'}</dd></div>
          <div class="full"><dt>Case numbers${cases.length > 12 ? ' (first 12 of ' + cases.length + ')' : ''}</dt><dd class="mono">${esc(cases.slice(0, 12).join(', '))}</dd></div>
          <div class="full"><dd><a class="link" href="https://www.fcmcclerk.com/reports/evictions" target="_blank" rel="noopener">Source: Franklin County Municipal Court Clerk eviction reports →</a></dd></div>
        </dl>`;
    },
  },
  {
    key: 'cleveland',
    label: 'Cleveland Code Enforcement',
    dataUrl: 'https://mauricewilli572000-cloud.github.io/cleveland-code-enforcement/cleveland_feed.json',
    tagline: "Not foreclosures: distressed-property signals in the City of Cleveland (condemnations, board-ups, housing violation notices, civil tickets) from the city's open data, matched to County owner and mailing records and scored for outreach. Showing leads scoring 40+; leads that also have a foreclosure filing are flagged.",
    csvUrl: 'https://mauricewilli572000-cloud.github.io/cleveland-code-enforcement/ghl_cleveland_code_enforcement.csv',
    // Lives in its own repo (cleveland-code-enforcement) and publishes cleveland_feed.json on its own GitHub Pages site.
    // One record = one PROPERTY with all of its open code enforcement signals merged.
    columns: [
      { key: 'score', label: 'Score', sortable: true },
      { key: 'owner', label: 'Owner', sortable: true },
      { key: 'prop_address', label: 'Property', sortable: false },
      { key: 'open_records', label: 'Open records', sortable: true },
      { key: 'oldest_open_days', label: 'Days open', sortable: true },
      { key: 'captured_date', label: 'Added', sortable: true },
      { key: 'mail_address', label: 'Mailing address', sortable: false },
      { key: 'flags', label: 'Flags', sortable: false },
    ],
    adapt(data) {
      const typeLabel = {
        'Active Condemnation': 'Condemned',
        'Board-Up (open/vacant/vandalized)': 'Boarded up',
        'Building Complaint Violation Notice': 'Violation notice',
        'Civil Ticket (Residents First)': 'Civil ticket',
      };
      const ownerLabel = { individual: 'Individual owner', entity: 'LLC / company', institutional: 'Institutional owner' };
      const meta = data.meta || {};
      const records = (data.leads || []).map(l => {
        const flags = (l.lead_types || []).map(t => typeLabel[t] || t);
        if (l.in_foreclosure) flags.push('Also in foreclosure');
        if (l.out_of_state_owner) flags.push('Out-of-state owner');
        if (ownerLabel[l.owner_class]) flags.push(ownerLabel[l.owner_class]);
        return {
          doc_num: l.lead_id,
          case_number: (l.case_numbers || []).join(', '),
          doc_type: (l.lead_types || []).join('; '),
          filed: l.latest_date,
          captured_date: l.added_date,
          owner: l.owner_name,
          grantee: '',
          prop_address: l.property_address,
          prop_city: 'Cleveland',
          prop_state: 'OH',
          prop_zip: l.zip,
          mail_address: l.mailing_address,   // already a full "street, city, state zip" string
          mail_city: '',
          mail_state: '',
          mail_zip: '',
          flags,
          score: l.score,
          open_records: l.open_records,
          oldest_open_days: l.oldest_open_days,
          raw: l,
        };
      });
      return {
        fetched_at: meta.updated_utc,
        total: meta.feed_count || records.length,
        with_address: meta.with_mailing || 0,
        new_this_run: meta.new_count || 0,
        records,
      };
    },
    rowHTML(r, flagsHTML) {
      const x = r.raw;
      return `
        <td><span class="score ${scoreClass(r.score)}">${r.score}</span></td>
        <td>${esc(r.owner) || '—'}</td>
        <td>${esc(r.prop_address) || '—'}<br><span class="muted">Cleveland${r.prop_zip ? ', ' + esc(r.prop_zip) : ''}</span></td>
        <td>${x.open_records}</td>
        <td>${x.oldest_open_days ? x.oldest_open_days + ' d' : '<span class="muted">—</span>'}</td>
        <td>${esc(r.captured_date) || '—'}</td>
        <td>${esc(r.mail_address) || '<span class="muted">not on file</span>'}</td>
        <td>${flagsHTML}</td>`;
    },
    detailHTML(r) {
      const x = r.raw;
      const cases = x.case_numbers || [];
      return `
        <dl class="detail-grid">
          <div class="full"><dt>Why it scored ${r.score}</dt><dd>${esc((x.score_reasons || []).join(' · '))}</dd></div>
          <div><dt>Open signals</dt><dd>${esc((x.lead_types || []).join(' · ')) || '—'}</dd></div>
          <div><dt>Violation / citation types</dt><dd>${esc((x.violation_types || []).join(' · ')) || '—'}</dd></div>
          <div><dt>Latest filing / oldest open</dt><dd>${esc(x.latest_date) || '—'} / ${x.oldest_open_days ? x.oldest_open_days + ' days' : '—'}</dd></div>
          <div><dt>Parcel</dt><dd class="mono">${esc(x.parcel) || '—'}</dd></div>
          <div><dt>Neighborhood / ward</dt><dd>${esc(x.neighborhood) || '—'} / ${esc(x.ward) || '—'}</dd></div>
          <div><dt>Owner record</dt><dd>${esc(x.owner_name) || '—'}</dd></div>
          <div class="full"><dt>Property address</dt><dd>${esc(r.prop_address) || '—'}, Cleveland, OH ${esc(r.prop_zip)}</dd></div>
          <div class="full"><dt>Mailing address</dt><dd>${esc(r.mail_address) || '—'}</dd></div>
          <div class="full"><dt>Case numbers${cases.length >= 10 ? ' (first 10)' : ''}</dt><dd class="mono">${esc(cases.join(', ')) || '—'}</dd></div>
          <div class="full"><dd><a class="link" href="https://data.clevelandohio.gov" target="_blank" rel="noopener">Source: City of Cleveland Open Data (Building &amp; Housing) →</a></dd></div>
        </dl>`;
    },
  },
  {
    key: 'excess',
    label: 'Franklin Excess Funds',
    dataUrl: 'https://mauricewilli572000-cloud.github.io/franklin-excess-funds/excess_funds.json',
    tagline: "Not foreclosure filings: money the Franklin County Clerk is holding from sheriff sales that sold for more than was owed (Excess Sales Proceeds report). The former owner or their heirs can usually claim it by motion. Checked daily; cases that drop off the Clerk's list are treated as claimed.",
    csvUrl: 'https://mauricewilli572000-cloud.github.io/franklin-excess-funds/excess_funds_ghl_export.csv',
    // Lives in its own repo (franklin-excess-funds) and publishes excess_funds.json on its own GitHub Pages site.
    // One record = one CASE with all of its deposits summed.
    columns: [
      { key: 'score', label: 'Score', sortable: true },
      { key: 'owner', label: 'Former owner', sortable: true },
      { key: 'prop_address', label: 'Property', sortable: false },
      { key: 'amount', label: 'Surplus held', sortable: true },
      { key: 'filed', label: 'Deposited', sortable: true },
      { key: 'captured_date', label: 'Added', sortable: true },
      { key: 'mail_address', label: 'Mailing address', sortable: false },
      { key: 'flags', label: 'Flags', sortable: false },
    ],
    adapt(data) {
      const records = (data.leads || []).map(l => ({
        doc_num: l.case_number,
        case_number: l.case_number,
        doc_type: 'Excess Sales Proceeds',
        filed: l.deposit_date,
        captured_date: l.first_seen,
        owner: l.owner,
        grantee: l.plaintiff,
        amount: l.amount,
        legal: '',
        prop_address: l.prop_address,
        prop_city: l.prop_city,
        prop_state: l.prop_state,
        prop_zip: l.prop_zip,
        mail_address: l.mail_address,
        mail_city: l.mail_city,
        mail_state: l.mail_state,
        mail_zip: l.mail_zip,
        flags: l.flags || [],
        score: l.score,
        raw: l,
      }));
      const total = data.total_surplus != null ? ' ($' + Number(data.total_surplus).toLocaleString() + ' held)' : '';
      return {
        fetched_at: data.generated_at ? data.generated_at + total : null,
        total: data.total_leads || 0,
        with_address: data.leads_with_mailing_address || 0,
        new_this_run: data.new_this_run || 0,
        records,
      };
    },
    detailHTML(r) {
      const x = r.raw;
      const extra = Object.entries(x.extra || {});
      return `
        <dl class="detail-grid">
          <div class="full"><dt>Why it scored ${r.score}</dt><dd>${esc((x.score_reasons || []).join(' · '))}</dd></div>
          <div><dt>Case number</dt><dd class="mono">${esc(x.case_number)}</dd></div>
          <div><dt>Surplus on deposit</dt><dd>${money(x.amount)}${x.row_count > 1 ? ` <span class="muted">(${x.row_count} deposits)</span>` : ''}</dd></div>
          <div><dt>Plaintiff</dt><dd>${esc(x.plaintiff) || '—'}${x.tax_foreclosure ? ' <span class="muted">(tax foreclosure)</span>' : ''}</dd></div>
          <div><dt>Owner type</dt><dd>${esc(x.owner_type) || '—'}</dd></div>
          <div><dt>Deposited / sale date</dt><dd>${esc(x.deposit_date) || '—'} / ${esc(x.sale_date) || '—'}</dd></div>
          <div><dt>First seen on Clerk list</dt><dd>${esc(x.first_seen) || '—'}</dd></div>
          <div class="full"><dt>All defendants (as listed)</dt><dd>${esc(x.defendants_raw || x.caption) || '—'}</dd></div>
          <div class="full"><dt>Property address${x.address_source ? ' <span class="muted">— ' + esc(x.address_source) + '</span>' : ''}</dt><dd>${esc(fullPropAddress(r)) || '—'}</dd></div>
          <div class="full"><dt>Mailing address</dt><dd>${esc(fullMailAddress(r)) || '—'}</dd></div>
          ${x.parcel_number ? `<div><dt>Parcel</dt><dd class="mono">${esc(x.parcel_number)}</dd></div>` : ''}
          ${extra.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}
          <div class="full"><dd>
            <a class="link" href="${esc(x.case_search_url)}" target="_blank" rel="noopener">Look up case ${esc(x.case_number)} (Clerk case search) →</a><br>
            <a class="link" href="${esc(x.source_url)}" target="_blank" rel="noopener">Source: Clerk's Excess Sales Proceeds report →</a><br>
            <a class="link" href="https://clerk.franklincountyohio.gov/Resources/Foreclosure-Excess-Fund-Process" target="_blank" rel="noopener">How funds are claimed (motion + hearing) →</a>
          </dd></div>
        </dl>`;
    },
  },
  {
    key: 'excess-national',
    label: 'Excess Funds (US)',
    dataUrl: 'https://mauricewilli572000-cloud.github.io/excess-funds-national/feeds/top.json',
    tagline: "Top 1,000 foreclosure and tax-sale surplus leads nationwide, from county lists found automatically. Open the full nationwide dashboard for every state, county filters and the source list.",
    csvUrl: 'https://mauricewilli572000-cloud.github.io/excess-funds-national/feeds/top_ghl.csv',
    // Lives in its own repo (excess-funds-national). Full dashboard: https://mauricewilli572000-cloud.github.io/excess-funds-national/
    columns: [
      { key: 'score', label: 'Score', sortable: true },
      { key: 'owner', label: 'Owner', sortable: true },
      { key: 'prop_address', label: 'County', sortable: false },
      { key: 'amount', label: 'Surplus', sortable: true },
      { key: 'filed', label: 'Sale / deposit', sortable: true },
      { key: 'captured_date', label: 'Added', sortable: true },
      { key: 'mail_address', label: 'Mailing address', sortable: false },
      { key: 'flags', label: 'Flags', sortable: false },
    ],
    adapt(data) {
      const records = (data.leads || []).map(l => ({
        doc_num: l.lead_id, case_number: l.case_number || l.parcel, doc_type: l.fund_type,
        filed: l.sale_date, captured_date: l.first_seen, owner: l.owner, grantee: l.plaintiff,
        amount: l.amount, prop_address: l.county + ', ' + l.state, prop_city: l.prop_address || '',
        prop_state: '', prop_zip: '', mail_address: l.mail_address, mail_city: l.mail_city,
        mail_state: l.mail_state, mail_zip: l.mail_zip, flags: l.flags || [], score: l.score, raw: l,
      }));
      return { fetched_at: data.generated_at, total: data.total_leads || 0,
               with_address: data.leads_with_mailing_address || 0, new_this_run: data.new_this_run || 0, records };
    },
    detailHTML(r) {
      const x = r.raw;
      return `<dl class="detail-grid">
        <div class="full"><dt>Why it scored ${r.score}</dt><dd>${esc((x.score_reasons || []).join(' · '))}</dd></div>
        <div><dt>County</dt><dd>${esc(x.county)}, ${esc(x.state)}</dd></div>
        <div><dt>Case / parcel</dt><dd class="mono">${esc(x.case_number || x.parcel) || '—'}</dd></div>
        <div><dt>Owner type</dt><dd>${esc(x.owner_type) || '—'}</dd></div>
        <div><dt>Surplus</dt><dd>${money(x.amount)}</dd></div>
        <div class="full"><dt>Name(s) as listed</dt><dd>${esc(x.owner_raw) || '—'}</dd></div>
        <div class="full"><dt>Property address</dt><dd>${esc(x.prop_address) || '—'}</dd></div>
        <div class="full"><dd><a class="link" href="${esc(x.source_url)}" target="_blank" rel="noopener">Source list →</a> &nbsp;
          <a class="link" href="https://mauricewilli572000-cloud.github.io/excess-funds-national/" target="_blank" rel="noopener">Full nationwide dashboard →</a></dd></div>
      </dl>`;
    },
  },
  {
    key: 'columbus',
    label: 'Columbus Code Enforcement',
    dataUrl: 'https://mauricewilli572000-cloud.github.io/columbus-code-enforcement/code_enforcement.json',
    tagline: "Not foreclosures: open City of Columbus code enforcement cases (vacant structures, exterior violations, overgrowth, repeat offenders) from the Accela permits portal, matched to Franklin County Auditor owner and mailing records and scored for outreach. Only active cases; apartments/units and noise complaints are excluded.",
    csvUrl: 'https://raw.githubusercontent.com/mauricewilli572000-cloud/columbus-code-enforcement/main/data/ghl_code_enforcement.csv',
    // Lives in its own repo (columbus-code-enforcement) and publishes docs/code_enforcement.json on its own GitHub Pages site.
    // One record = one open enforcement CASE.
    columns: [
      { key: 'score', label: 'Score', sortable: true },
      { key: 'owner', label: 'Owner', sortable: true },
      { key: 'prop_address', label: 'Property', sortable: false },
      { key: 'repeat', label: 'Repeat cases', sortable: true },
      { key: 'filed', label: 'Opened', sortable: true },
      { key: 'captured_date', label: 'Added', sortable: true },
      { key: 'mail_address', label: 'Mailing address', sortable: false },
      { key: 'flags', label: 'Flags', sortable: false },
    ],
    adapt(data) {
      const records = (data.leads || []).map(l => {
        const flags = (l.lead_tags || '').split(',').map(t => t.trim()).filter(Boolean)
          .filter(t => t !== 'open' && !/^repeat-\d+$/.test(t))
          .map(t => ({ absentee: 'Absentee owner', 'exterior-violation': 'Exterior violation' }[t] || (t.charAt(0).toUpperCase() + t.slice(1))));
        if (l.repeat_violations > 1) flags.push(`${l.repeat_violations} cases at address`);
        if (l.foreclosure_match) flags.push('Also in foreclosure');
        if (l.mailing_state && l.mailing_state !== 'OH') flags.push('Out-of-state owner');
        return {
          doc_num: l.case_number,
          case_number: l.case_number,
          doc_type: l.record_type,
          filed: l.filing_date,
          captured_date: l.date_added,
          owner: l.owner_name,
          grantee: '',
          prop_address: l.property_address,
          prop_city: l.property_city,
          prop_state: l.property_state,
          prop_zip: l.property_zip,
          mail_address: l.mailing_address,
          mail_city: l.mailing_city,
          mail_state: l.mailing_state,
          mail_zip: l.mailing_zip,
          flags,
          score: l.lead_score,
          repeat: l.repeat_violations || 0,
          raw: l,
        };
      });
      return {
        fetched_at: data.updated,
        total: data.count || records.length,
        with_address: records.filter(r => r.mail_address).length,
        new_this_run: records.filter(r => r.captured_date && data.updated && r.captured_date === String(data.updated).slice(0, 10)).length,
        records,
      };
    },
    rowHTML(r, flagsHTML) {
      return `
        <td><span class="score ${scoreClass(r.score)}">${r.score}</span></td>
        <td>${esc(r.owner) || '<span class="muted">not matched</span>'}</td>
        <td>${esc(r.prop_address) || '—'}<br><span class="muted">${esc(r.prop_city || 'Columbus')}${r.prop_zip ? ', ' + esc(r.prop_zip) : ''}</span></td>
        <td>${r.repeat || '—'}</td>
        <td>${esc(r.filed) || '—'}</td>
        <td>${esc(r.captured_date) || '—'}</td>
        <td>${esc(fullMailAddress(r)) || '<span class="muted">not on file</span>'}</td>
        <td>${flagsHTML}</td>`;
    },
    detailHTML(r) {
      const x = r.raw;
      return `
        <dl class="detail-grid">
          <div class="full"><dt>Case type</dt><dd>${esc(x.record_type) || '—'} <span class="muted">(${esc(x.record_status) || '—'})</span></dd></div>
          <div class="full"><dt>Inspector's description</dt><dd>${esc(x.description) || '—'}</dd></div>
          <div><dt>Case number</dt><dd class="mono">${esc(x.case_number)}</dd></div>
          <div><dt>Opened</dt><dd>${esc(x.filing_date) || '—'}</dd></div>
          <div><dt>Cases at this address</dt><dd>${x.repeat_violations || 1}</dd></div>
          <div><dt>Absentee owner</dt><dd>${x.absentee_owner ? 'Yes' : 'No'}</dd></div>
          <div><dt>Last sale</dt><dd>${esc(x.last_sale_date) || '—'}${x.last_sale_price && x.last_sale_price !== '0' ? ' · ' + money(Number(x.last_sale_price)) : ''}</dd></div>
          <div><dt>Year built / land use</dt><dd>${esc(x.year_built) || '—'} / ${esc(x.land_use) || '—'}${x.homestead ? ' · homestead' : ''}</dd></div>
          ${x.foreclosure_match ? `<div class="full"><dt>Foreclosure match</dt><dd>${esc(x.foreclosure_match)} ${esc(x.foreclosure_case_number)}</dd></div>` : ''}
          <div class="full"><dt>Property address</dt><dd>${esc(fullPropAddress(r)) || '—'}</dd></div>
          <div class="full"><dt>Mailing address</dt><dd>${esc(fullMailAddress(r)) || '—'}</dd></div>
          <div class="full"><dd><a class="link" href="${esc(x.source_url)}" target="_blank" rel="noopener">Open case on the Columbus permits portal →</a></dd></div>
        </dl>`;
    },
  },
  {
    key: 'agents',
    label: 'Agent Roster',
    dataUrl: 'https://mauricewilli572000-cloud.github.io/agent-roster-scraper/data/records.json',
    tagline: "Not property leads: licensed real estate agents in Franklin, Cuyahoga, Summit and Hamilton counties, with direct phone numbers where found, ranked by how many distressed, well-under-market or stale (120+ days) listings they currently have. Use for off-market outreach.",
    csvUrl: 'https://raw.githubusercontent.com/mauricewilli572000-cloud/agent-roster-scraper/main/exports/ghl_distressed_agents.csv',
    // Lives in its own repo (agent-roster-scraper) and publishes docs/data/records.json on its own GitHub Pages site.
    // One record = one AGENT. Its own dashboard: https://mauricewilli572000-cloud.github.io/agent-roster-scraper/
    columns: [
      { key: 'score', label: 'Distress score', sortable: true },
      { key: 'owner', label: 'Agent', sortable: true },
      { key: 'prop_address', label: 'Brokerage', sortable: false },
      { key: 'flagged', label: 'Flagged listings', sortable: true },
      { key: 'county', label: 'County', sortable: true },
      { key: 'captured_date', label: 'Added', sortable: true },
      { key: 'mail_address', label: 'Phone / email', sortable: false },
      { key: 'flags', label: 'Flags', sortable: false },
    ],
    adapt(data) {
      const list = Array.isArray(data) ? data : (data.records || data.agents || []);
      const records = list.map(a => {
        const flags = [];
        if (a.distressed_listings) flags.push(`${a.distressed_listings} distressed`);
        if (a.under_market_listings) flags.push(`${a.under_market_listings} under market`);
        if (a.stale_listings) flags.push(`${a.stale_listings} stale 120d+`);
        if (a.new_listings) flags.push(`${a.new_listings} new`);
        if (a.lead_match_listings) flags.push('Also in foreclosure');
        if (a.phone) flags.push('Has phone');
        if (a.license_check && a.license_check !== 'ok' && a.license_check !== 'verified') flags.push('License: ' + a.license_check);
        return {
          doc_num: a.id || a.license_number || a.name,
          case_number: a.license_number || '',
          doc_type: a.license_type || 'Agent',
          filed: a.last_listing || '',
          captured_date: a.first_seen || '',
          owner: a.name,
          grantee: '',
          prop_address: a.brokerage || '',
          prop_city: a.city || '', prop_state: a.state || '', prop_zip: a.zip || '',
          mail_address: [a.phone, a.email].filter(Boolean).join(' · '),
          mail_city: '', mail_state: '', mail_zip: '',
          county: a.county || '',
          flagged: a.flagged_listings || 0,
          flags,
          score: Math.round(a.distress_score || 0),
          raw: a,
        };
      });
      // records.json is a bare list (the timestamp lives in meta.json), so use the newest last_seen
      const seen = list.map(a => a.last_seen || a.first_seen || '').filter(Boolean).sort();
      return {
        fetched_at: (data && data.generated_at) || seen[seen.length - 1] || (records.length ? 'see agent dashboard' : null),
        total: records.length,
        with_address: records.filter(r => r.raw.phone).length,
        new_this_run: 0,
        records,
      };
    },
    rowHTML(r, flagsHTML) {
      const x = r.raw;
      return `
        <td><span class="score ${scoreClass(r.score)}">${r.score}</span></td>
        <td>${esc(r.owner) || '—'}<br><span class="muted">${esc(x.license_type) || ''}</span></td>
        <td>${esc(r.prop_address) || '<span class="muted">—</span>'}${x.city ? `<br><span class="muted">${esc(x.city)}</span>` : ''}</td>
        <td>${r.flagged || '—'}</td>
        <td>${esc(r.county) || '—'}</td>
        <td>${esc(r.captured_date) || '—'}</td>
        <td>${x.phone ? `<a class="link" href="tel:${esc(x.phone)}">${esc(x.phone)}</a>` : '<span class="muted">no phone</span>'}${x.email ? `<br><span class="muted">${esc(x.email)}</span>` : ''}</td>
        <td>${flagsHTML}</td>`;
    },
    detailHTML(r) {
      const x = r.raw;
      return `
        <dl class="detail-grid">
          <div><dt>License</dt><dd class="mono">${esc(x.license_number) || '—'}</dd></div>
          <div><dt>Type / expires</dt><dd>${esc(x.license_type) || '—'} / ${esc(x.license_expires || x.expiration) || '—'}</dd></div>
          <div><dt>License check</dt><dd>${esc(x.license_check) || '—'}</dd></div>
          <div><dt>Brokerage (license record)</dt><dd>${esc(x.license_brokerage || x.brokerage) || '—'}</dd></div>
          <div><dt>Phone (source)</dt><dd>${esc(x.phone) || '—'} <span class="muted">${esc(x.phone_source) || ''}</span></dd></div>
          <div><dt>Email</dt><dd>${esc(x.email) || '—'}</dd></div>
          <div><dt>Listings: flagged / distressed / under market / stale / new</dt>
            <dd>${x.flagged_listings || 0} / ${x.distressed_listings || 0} / ${x.under_market_listings || 0} / ${x.stale_listings || 0} / ${x.new_listings || 0}</dd></div>
          <div><dt>Last flagged listing</dt><dd>${esc(x.last_listing) || '—'}</dd></div>
          <div><dt>First / last seen</dt><dd>${esc(x.first_seen) || '—'} / ${esc(x.last_seen) || '—'}</dd></div>
          <div class="full"><dd><a class="link" href="https://mauricewilli572000-cloud.github.io/agent-roster-scraper/" target="_blank" rel="noopener">Open the full agent roster dashboard →</a></dd></div>
        </dl>`;
    },
  },
];

// Each dashboard page sets window.PAGE = { heading, keys: [...] } before loading this file;
// only the tabs named in keys are shown on that page.
const PAGE = window.PAGE || {};
const COUNTIES = PAGE.keys
  ? PAGE.keys.map(k => ALL_COUNTIES.find(c => c.key === k)).filter(Boolean)
  : ALL_COUNTIES;

let ACTIVE_COUNTY = COUNTIES[0];
let ALL_RECORDS = [];
let SORT_KEY = 'score';
let SORT_DIR = -1; // -1 desc, 1 asc
let EXPANDED_DOC_NUM = null;

function scoreClass(s) {
  if (s >= 70) return 'score-hi';
  if (s >= 45) return 'score-mid';
  return 'score-lo';
}

function rateValue(r) {
  const n = Number(r.interest_rate);
  return r.interest_rate != null && r.interest_rate !== '' && n > 0 ? n : null;
}

function fmtRate(n) {
  return parseFloat(n.toFixed(3)) + '%';
}

function lowRateMax() {
  return Number(document.getElementById('lowRateMax').value);
}

function isLowRate(r) {
  const n = rateValue(r);
  return n != null && n <= lowRateMax();
}

function money(n) {
  return n != null ? '$' + Number(n).toLocaleString() : '—';
}

function esc(s) {
  return (s ?? '').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function fullMailAddress(r) {
  const parts = [r.mail_address, r.mail_city, [r.mail_state, r.mail_zip].filter(Boolean).join(' ')].filter(Boolean);
  return parts.join(', ');
}

function fullPropAddress(r) {
  const parts = [r.prop_address, r.prop_city, [r.prop_state, r.prop_zip].filter(Boolean).join(' ')].filter(Boolean);
  return parts.join(', ');
}

function detailHTML(r) {
  return `
    <dl class="detail-grid">
      <div><dt>Case number</dt><dd class="mono">${esc(r.case_number) || '—'}</dd></div>
      <div><dt>Document type</dt><dd>${esc(r.doc_type) || '—'}</dd></div>
      <div><dt>Plaintiff / lender</dt><dd>${esc(r.grantee) || '—'}</dd></div>
      <div><dt>Loan type</dt><dd>${esc(r.loan_type) || '—'}</dd></div>
      <div><dt>Interest rate</dt><dd>${rateValue(r) != null ? fmtRate(rateValue(r)) : '—'}</dd></div>
      <div><dt>Document number</dt><dd class="mono">${esc(r.doc_num)}</dd></div>
      <div class="full"><dt>Property address</dt><dd>${esc(fullPropAddress(r)) || '—'}</dd></div>
      <div class="full"><dt>Mailing address</dt><dd>${esc(fullMailAddress(r)) || '—'}</dd></div>
      ${r.legal ? `<div class="full"><dt>Legal description</dt><dd>${esc(r.legal)}</dd></div>` : ''}
      <div class="full"><dd><a class="link" href="${esc(r.clerk_url)}" target="_blank" rel="noopener">View filing on the Clerk of Courts site →</a></dd></div>
    </dl>
  `;
}

function render(list) {
  const wrap = document.getElementById('table-wrap');
  const empty = document.getElementById('empty');

  if (!list.length) {
    wrap.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const cols = ACTIVE_COUNTY.columns || COLUMNS;
  for (const col of cols) {
    const th = document.createElement('th');
    th.textContent = col.label;
    if (col.sortable) {
      if (col.key === SORT_KEY) {
        th.classList.add('sorted');
        th.innerHTML = `${col.label} <span class="arrow">${SORT_DIR === 1 ? '↑' : '↓'}</span>`;
      }
      th.addEventListener('click', () => {
        if (SORT_KEY === col.key) SORT_DIR *= -1;
        else { SORT_KEY = col.key; SORT_DIR = -1; }
        applyFilters();
      });
    }
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const r of list) {
    const tr = document.createElement('tr');
    const rate = rateValue(r);
    const low = !ACTIVE_COUNTY.columns && isLowRate(r);
    tr.className = 'row' + (r.doc_num === EXPANDED_DOC_NUM ? ' expanded' : '') + (low ? ' lowrate' : '');
    const flagsHTML = (r.flags || []).map(f =>
      `<span class="flag${/lis pendens|pre-foreclosure|probate|out-of-state|also in foreclosure|heirs|25k/i.test(f) ? ' hot' : ''}">${esc(f)}</span>`
    ).join('');
    tr.innerHTML = ACTIVE_COUNTY.rowHTML ? ACTIVE_COUNTY.rowHTML(r, flagsHTML) : `
      <td><span class="score ${scoreClass(r.score)}">${r.score}</span></td>
      <td>${esc(r.owner) || '—'}</td>
      <td>${esc(r.prop_address) || '—'}${r.prop_city ? `<br><span class="muted">${esc(r.prop_city)}${r.prop_zip ? ', ' + esc(r.prop_zip) : ''}</span>` : ''}</td>
      <td class="amount">${money(r.amount)}</td>
      <td class="rate">${rate != null ? `<span class="rate${low ? ' low' : ''}">${fmtRate(rate)}</span>` : '<span class="muted">—</span>'}</td>
      <td>${esc(r.filed) || '—'}</td>
      <td>${esc(r.captured_date) || '<span class="muted" title="Added before date tracking began">—</span>'}</td>
      <td>${esc(fullMailAddress(r)) || '<span class="muted">not matched</span>'}</td>
      <td>${flagsHTML}</td>
    `;
    tr.addEventListener('click', () => {
      EXPANDED_DOC_NUM = EXPANDED_DOC_NUM === r.doc_num ? null : r.doc_num;
      applyFilters();
    });
    tbody.appendChild(tr);

    if (r.doc_num === EXPANDED_DOC_NUM) {
      const detailTr = document.createElement('tr');
      detailTr.className = 'detail-row';
      const td = document.createElement('td');
      td.colSpan = cols.length;
      td.innerHTML = (ACTIVE_COUNTY.detailHTML || detailHTML)(r);
      detailTr.appendChild(td);
      tbody.appendChild(detailTr);
    }
  }
  table.appendChild(tbody);
  wrap.innerHTML = '';
  wrap.appendChild(table);
}

function applyFilters() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const minScore = Number(document.getElementById('minScore').value);
  const flag = document.getElementById('flagFilter').value;
  const addressOnly = document.getElementById('addressOnly').checked;

  // Rate controls only apply to the foreclosure tabs (default columns); other tabs have no loan data.
  const hasRates = !ACTIVE_COUNTY.columns;
  document.getElementById('rateControls').style.display = hasRates ? 'contents' : 'none';
  const lowRateOnly = hasRates && document.getElementById('lowRateOnly').checked;
  if (hasRates) {
    const n = ALL_RECORDS.filter(isLowRate).length;
    document.getElementById('lowRateCount').textContent = `(${n})`;
  }

  let list = ALL_RECORDS.filter(r => {
    if (lowRateOnly && !isLowRate(r)) return false;
    if (r.score < minScore) return false;
    if (flag && !(r.flags || []).includes(flag)) return false;
    if (addressOnly && !(r.prop_address || r.mail_address)) return false;
    if (q) {
      const hay = [r.owner, r.grantee, r.prop_address, r.mail_address, r.doc_num, r.case_number]
        .filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  list.sort((a, b) => {
    let av = a[SORT_KEY], bv = b[SORT_KEY];
    const numeric = ['amount', 'interest_rate', 'score', 'filings_12m', 'property_count', 'open_records', 'oldest_open_days'].includes(SORT_KEY);
    if (SORT_KEY === 'interest_rate') {
      // Leads with no rate on file always sort to the bottom, whichever direction.
      const none = SORT_DIR === 1 ? Infinity : -Infinity;
      av = rateValue(a) ?? none; bv = rateValue(b) ?? none;
    }
    if (av == null) av = numeric ? -Infinity : '';
    if (bv == null) bv = numeric ? -Infinity : '';
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av < bv) return -1 * SORT_DIR;
    if (av > bv) return 1 * SORT_DIR;
    return 0;
  });

  render(list);
}

function populateFlagFilter(records) {
  const select = document.getElementById('flagFilter');
  select.innerHTML = '<option value="">Any flag</option>';
  select.value = '';
  const seen = new Set();
  for (const r of records) for (const f of (r.flags || [])) seen.add(f);
  for (const f of Array.from(seen).sort()) {
    const opt = document.createElement('option');
    opt.value = f;
    opt.textContent = f;
    select.appendChild(opt);
  }
}

const DATA_CACHE = {};
let FILTERS_WIRED = false;

function renderTabs() {
  const tabsEl = document.getElementById('county-tabs');
  tabsEl.innerHTML = COUNTIES.map(c => `
    <button role="tab" data-key="${c.key}" aria-selected="${c.key === ACTIVE_COUNTY.key}">${esc(c.label)}</button>
  `).join('');
  tabsEl.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.dataset.key === ACTIVE_COUNTY.key) return;
      ACTIVE_COUNTY = COUNTIES.find(c => c.key === btn.dataset.key);
      EXPANDED_DOC_NUM = null;
      SORT_KEY = 'score'; SORT_DIR = -1;
      renderTabs();
      loadActive();
    });
  });
}

async function loadActive() {
  const county = ACTIVE_COUNTY;
  const stats = document.getElementById('stats');
  document.getElementById('tagline').textContent = county.tagline;
  document.getElementById('rateControls').style.display = county.columns ? 'none' : 'contents';
  stats.textContent = `Loading ${county.label} records…`;
  document.getElementById('empty').style.display = 'none';
  document.getElementById('table-wrap').innerHTML = '';

  let data;
  try {
    if (DATA_CACHE[county.key]) {
      data = DATA_CACHE[county.key];
    } else {
      const res = await fetch(county.dataUrl, { cache: 'no-store' });
      if (!res.ok) throw new Error(`${res.status} fetching ${county.dataUrl}`);
      const raw = await res.json();
      data = county.adapt(raw);
      DATA_CACHE[county.key] = data;
    }
  } catch (err) {
    stats.textContent = `Failed to load ${county.label} data: ` + err;
    return;
  }

  if (!data.fetched_at) {
    stats.textContent = 'No scrape has run yet for ' + county.label + '.';
    ALL_RECORDS = [];
    document.getElementById('empty').style.display = 'block';
    return;
  }

  stats.innerHTML = `<strong>${data.total}</strong> leads on file, <strong>${data.with_address}</strong> with a mailing address.
    ${data.new_this_run ? `<strong>${data.new_this_run}</strong> new as of the last run.` : ''}
    Last fetched ${esc(data.fetched_at)}. <a href="${esc(county.csvUrl)}">Download CSV for GoHighLevel →</a>`;

  ALL_RECORDS = data.records;
  populateFlagFilter(ALL_RECORDS);
  applyFilters();

  if (!FILTERS_WIRED) {
    FILTERS_WIRED = true;
    document.getElementById('search').addEventListener('input', applyFilters);
    document.getElementById('minScore').addEventListener('change', applyFilters);
    document.getElementById('flagFilter').addEventListener('change', applyFilters);
    document.getElementById('addressOnly').addEventListener('change', applyFilters);
    document.getElementById('lowRateMax').addEventListener('change', applyFilters);
    document.getElementById('lowRateOnly').addEventListener('change', applyFilters);
  }
}

function renderPageChrome() {
  if (PAGE.heading) {
    document.querySelector('header h1').textContent = PAGE.heading;
    document.title = PAGE.heading + ' · Motivated Seller Leads';
  }
  const nav = document.getElementById('dash-nav');
  if (nav && window.DASHBOARDS) {
    nav.innerHTML = '<a href="index.html" class="home">All dashboards</a>' +
      window.DASHBOARDS.filter(d => d.url).map(d =>
        `<a href="${esc(d.url)}"${d.id === PAGE.id ? ' aria-current="page"' : ''}>${esc(d.label)}</a>`
      ).join('');
  }
  if (COUNTIES.length < 2) document.getElementById('county-tabs').style.display = 'none';
}

renderPageChrome();
renderTabs();
loadActive();
