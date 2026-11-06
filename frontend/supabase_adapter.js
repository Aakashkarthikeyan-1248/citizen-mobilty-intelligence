/**
 * ============================================================
 * CityLens — Supabase Database Adapter
 * ============================================================
 *
 * DROP-IN REPLACEMENT for the localStorage Store object inside citylens.html.
 * When you have your Supabase URL and anon key ready:
 *
 *   1. Install the Supabase JS client:
 *        <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
 *
 *   2. Replace the two constants below with your project values.
 *
 *   3. In citylens.html, delete the entire localStorage `Store` object
 *      and paste this file's SupabaseStore object in its place.
 *
 *   4. Send your Supabase credentials and we will wire this together
 *      automatically.
 *
 * DATABASE TABLES REQUIRED (paste into Supabase SQL editor):
 * ----------------------------------------------------------
 *
 *   -- Users (Supabase Auth handles passwords; this is the profile table)
 *   CREATE TABLE public.cmi_users (
 *     id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
 *     name        TEXT NOT NULL,
 *     email       TEXT NOT NULL UNIQUE,
 *     role        TEXT NOT NULL DEFAULT 'citizen'  CHECK (role IN ('citizen','admin')),
 *     status      TEXT NOT NULL DEFAULT 'active'   CHECK (status IN ('active','pending','rejected')),
 *     employee_id TEXT,
 *     credits     INT  NOT NULL DEFAULT 0,
 *     created_at  TIMESTAMPTZ DEFAULT now()
 *   );
 *   ALTER TABLE public.cmi_users ENABLE ROW LEVEL SECURITY;
 *   CREATE POLICY "Users read own row"   ON public.cmi_users FOR SELECT USING (auth.uid() = id);
 *   CREATE POLICY "Admins read all"      ON public.cmi_users FOR SELECT USING (
 *     EXISTS (SELECT 1 FROM public.cmi_users WHERE id = auth.uid() AND role = 'admin')
 *   );
 *
 *   -- Incidents
 *   CREATE TABLE public.cmi_incidents (
 *     id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
 *     title           TEXT NOT NULL,
 *     cat             TEXT NOT NULL,
 *     sev             TEXT NOT NULL,
 *     loc             TEXT,
 *     lat             DOUBLE PRECISION NOT NULL,
 *     lng             DOUBLE PRECISION NOT NULL,
 *     source          TEXT NOT NULL DEFAULT 'citizen',
 *     reporter_id     UUID REFERENCES public.cmi_users(id),
 *     description     TEXT,
 *     photo_url       TEXT,
 *     confidence      DOUBLE PRECISION,
 *     status          TEXT NOT NULL DEFAULT 'pending',
 *     verified_by     UUID[] DEFAULT '{}',
 *     history         JSONB  DEFAULT '[]',
 *     reported_at     TIMESTAMPTZ DEFAULT now(),
 *     updated_at      TIMESTAMPTZ DEFAULT now()
 *   );
 *   ALTER TABLE public.cmi_incidents ENABLE ROW LEVEL SECURITY;
 *   CREATE POLICY "Anyone can read active incidents" ON public.cmi_incidents FOR SELECT USING (true);
 *   CREATE POLICY "Citizens insert own"              ON public.cmi_incidents FOR INSERT WITH CHECK (auth.uid() = reporter_id);
 *   CREATE POLICY "Admins update any"               ON public.cmi_incidents FOR UPDATE USING (
 *     EXISTS (SELECT 1 FROM public.cmi_users WHERE id = auth.uid() AND role = 'admin')
 *   );
 *
 *   -- Credit ledger (append-only, idempotent by event_id)
 *   CREATE TABLE public.cmi_ledger (
 *     event_id    TEXT PRIMARY KEY,
 *     user_id     UUID REFERENCES public.cmi_users(id),
 *     amount      INT NOT NULL,
 *     reason      TEXT,
 *     created_at  TIMESTAMPTZ DEFAULT now()
 *   );
 * ============================================================
 */

// ── FILL THESE IN ────────────────────────────────────────────
const SUPABASE_URL  = 'https://YOUR_PROJECT_ID.supabase.co';
const SUPABASE_ANON = 'YOUR_ANON_PUBLIC_KEY';
// ─────────────────────────────────────────────────────────────

const _sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON);

/**
 * SupabaseStore — same public API as the localStorage Store in citylens.html.
 * Swap this object for `Store` after you have connected Supabase.
 */
const SupabaseStore = {

  // ── Auth ──────────────────────────────────────────────────

  async signUp(name, email, password, role = 'citizen', employeeId = null) {
    const { data, error } = await _sb.auth.signUp({ email, password });
    if (error) throw new Error(error.message);
    // Insert profile row (triggers RLS)
    await _sb.from('cmi_users').insert({
      id: data.user.id, name, email, role,
      status: role === 'admin' ? 'pending' : 'active',
      employee_id: employeeId,
    });
    return data.user;
  },

  async signIn(email, password) {
    const { data, error } = await _sb.auth.signInWithPassword({ email, password });
    if (error) throw new Error('Email or password is incorrect.');
    return data.user;
  },

  async signOut() {
    await _sb.auth.signOut();
  },

  async getSession() {
    const { data } = await _sb.auth.getSession();
    return data?.session ?? null;
  },

  // ── Users ─────────────────────────────────────────────────

  async getUsers() {
    const { data, error } = await _sb.from('cmi_users').select('*');
    if (error) throw new Error(error.message);
    return data;
  },

  async getUserById(id) {
    const { data } = await _sb.from('cmi_users').select('*').eq('id', id).single();
    return data;
  },

  async updateUserStatus(userId, status) {
    const { error } = await _sb.from('cmi_users').update({ status }).eq('id', userId);
    if (error) throw new Error(error.message);
  },

  // ── Incidents ─────────────────────────────────────────────

  async getIncidents() {
    const { data, error } = await _sb
      .from('cmi_incidents')
      .select('*')
      .order('reported_at', { ascending: false });
    if (error) throw new Error(error.message);
    // Normalize snake_case → camelCase to match existing UI expectations
    return (data || []).map(r => ({
      id:          r.id,
      title:       r.title,
      cat:         r.cat,
      sev:         r.sev,
      loc:         r.loc,
      lat:         r.lat,
      lng:         r.lng,
      source:      r.source,
      reporterId:  r.reporter_id,
      description: r.description,
      photo:       r.photo_url,
      confidence:  r.confidence,
      status:      r.status,
      verifiedBy:  r.verified_by || [],
      history:     r.history    || [],
      reportedAt:  new Date(r.reported_at).getTime(),
    }));
  },

  async insertIncident(inc) {
    const { error } = await _sb.from('cmi_incidents').insert({
      id:          inc.id,
      title:       inc.title,
      cat:         inc.cat,
      sev:         inc.sev,
      loc:         inc.loc,
      lat:         inc.lat,
      lng:         inc.lng,
      source:      inc.source,
      reporter_id: inc.reporterId,
      description: inc.description,
      photo_url:   inc.photo,
      confidence:  inc.confidence,
      status:      inc.status,
      verified_by: inc.verifiedBy,
      history:     inc.history,
      reported_at: new Date(inc.reportedAt).toISOString(),
    });
    if (error) throw new Error(error.message);
  },

  async updateIncident(id, patch) {
    // Convert camelCase patch keys → snake_case column names
    const row = {};
    if (patch.status      !== undefined) row.status      = patch.status;
    if (patch.verifiedBy  !== undefined) row.verified_by = patch.verifiedBy;
    if (patch.history     !== undefined) row.history      = patch.history;
    row.updated_at = new Date().toISOString();
    const { error } = await _sb.from('cmi_incidents').update(row).eq('id', id);
    if (error) throw new Error(error.message);
  },

  // ── Credit Ledger ─────────────────────────────────────────

  async addCredit(entry) {
    // Idempotent: ON CONFLICT DO NOTHING (event_id is PK)
    const { error } = await _sb.from('cmi_ledger').upsert(
      { event_id: entry.id, user_id: entry.userId, amount: entry.amount, reason: entry.reason },
      { onConflict: 'event_id', ignoreDuplicates: true }
    );
    if (error) throw new Error(error.message);
    // Also update cached credit total on the user row
    await _sb.rpc('increment_credits', { uid: entry.userId, delta: entry.amount });
  },

  async getCreditsFor(userId) {
    const { data } = await _sb.from('cmi_users').select('credits').eq('id', userId).single();
    return data?.credits ?? 0;
  },

  // ── Real-time subscription ─────────────────────────────────
  /**
   * Subscribe to new / updated incidents.
   * Call this once after the user is logged in.
   *
   * @param {function} onInsert  Called with the new incident (camelCase)
   * @param {function} onUpdate  Called with the updated incident (camelCase)
   */
  subscribeIncidents(onInsert, onUpdate) {
    return _sb
      .channel('cmi-incidents-realtime')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'cmi_incidents' }, payload => {
        onInsert && onInsert(payload.new);
      })
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'cmi_incidents' }, payload => {
        onUpdate && onUpdate(payload.new);
      })
      .subscribe();
  },

  unsubscribe(channel) {
    _sb.removeChannel(channel);
  },
};

// Export for use in modules or attach to window for plain-HTML usage
if (typeof module !== 'undefined') module.exports = SupabaseStore;
else window.SupabaseStore = SupabaseStore;
