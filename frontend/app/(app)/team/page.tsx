'use client'
import { useEffect, useState } from 'react'
import {
  Users, Shield, ChevronDown, Check, X, Loader2,
  AlertCircle, Edit3, UserCheck, UserX, Lock, UserPlus, Clock,
} from 'lucide-react'
import { brainwaveApi, type BrainwaveUserRow, type ProfileUpsertPayload } from '@/lib/api'

// ─── Constants ────────────────────────────────────────────────────────────────

const BRAINWAVE_ROLES = [
  { value: 'qualifying_specialist', label: 'Qualifying Specialist' },
  { value: 'client_advisor',        label: 'Client Advisor' },
  { value: 'placement_specialist',  label: 'Placement Specialist' },
  { value: 'relationship_manager',  label: 'Relationship Manager' },
  { value: 'vp',                    label: 'VP' },
  { value: 'admin',                 label: 'Administrator' },
]

const ROLE_COLORS: Record<string, string> = {
  qualifying_specialist: 'bg-violet-100 text-violet-700',
  client_advisor:        'bg-blue-100 text-blue-700',
  placement_specialist:  'bg-teal-100 text-teal-700',
  relationship_manager:  'bg-orange-100 text-orange-700',
  vp:                    'bg-rose-100 text-rose-700',
  admin:                 'bg-gray-200 text-gray-700',
}

// ─── Shared form fields component ────────────────────────────────────────────

function ProfileFields({
  role, setRole,
  dbName, setDbName,
  qualifierId, setQualifierId,
  canImpersonate, setCanImpersonate,
}: {
  role: string; setRole: (v: string) => void
  dbName: string; setDbName: (v: string) => void
  qualifierId: string; setQualifierId: (v: string) => void
  canImpersonate: boolean; setCanImpersonate: (v: boolean) => void
}) {
  const needsDbName = role && !['vp', 'admin'].includes(role)

  return (
    <div className="space-y-4">
      {/* Role */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1.5">
          Brainwave Role <span className="text-red-400">*</span>
        </label>
        <div className="relative">
          <select value={role} onChange={e => setRole(e.target.value)}
            className="w-full appearance-none border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 pr-8">
            <option value="">Select a role…</option>
            {BRAINWAVE_ROLES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
          <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
        </div>
      </div>

      {/* DB Name */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1.5">
          Name in Database
          {needsDbName && <span className="ml-1 text-red-400">*</span>}
        </label>
        <input type="text" value={dbName} onChange={e => setDbName(e.target.value)}
          placeholder="e.g. Rita Mason"
          className={`w-full border rounded-lg px-3 py-2.5 text-sm placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 ${
            needsDbName && !dbName.trim() ? 'border-amber-300 bg-amber-50' : 'border-gray-200'
          }`} />
        <p className="text-xs text-gray-400 mt-1">
          Must exactly match how this person appears in Brainwave's ownership columns
          (clientadvisor, qualifiername, placementspecialist, etc.)
          {needsDbName && !dbName.trim() && (
            <span className="text-amber-600"> — required for this role</span>
          )}
        </p>
      </div>

      {/* Qualifier ID */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1.5">
          Qualifier ID
          <span className="ml-1.5 text-xs font-normal text-gray-400">(optional — for Qualifying Specialists)</span>
        </label>
        <input type="number" value={qualifierId} onChange={e => setQualifierId(e.target.value)}
          placeholder="e.g. 142"
          className="w-full border border-gray-200 rounded-lg px-3 py-2.5 text-sm placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500" />
        <p className="text-xs text-gray-400 mt-1">bqp_user.userid — used for bqp_interview.qualifierid joins.</p>
      </div>

      {/* Can Impersonate */}
      <div className="flex items-start gap-3 p-3 rounded-xl border border-amber-100 bg-amber-50">
        <button type="button" onClick={() => setCanImpersonate(!canImpersonate)}
          className={`mt-0.5 w-10 h-5 rounded-full relative transition-colors flex-shrink-0 ${canImpersonate ? 'bg-amber-500' : 'bg-gray-300'}`}>
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${canImpersonate ? 'translate-x-5' : 'translate-x-0.5'}`} />
        </button>
        <div>
          <p className="text-sm font-medium text-gray-800">Admin — Can Impersonate</p>
          <p className="text-xs text-gray-500 mt-0.5">
            Grants access to this admin panel and allows testing other roles via
            the X-Impersonate-Role header. Grant only to developers.
          </p>
        </div>
      </div>
    </div>
  )
}

// ─── Edit modal (existing Visually user) ─────────────────────────────────────

function EditModal({
  row, onSave, onClose,
}: { row: BrainwaveUserRow; onSave: (u: BrainwaveUserRow) => void; onClose: () => void }) {
  const [role,           setRole]           = useState(row.brainwave_role ?? '')
  const [dbName,         setDbName]         = useState(row.db_name ?? '')
  const [qualifierId,    setQualifierId]    = useState(row.qualifier_id != null ? String(row.qualifier_id) : '')
  const [canImpersonate, setCanImpersonate] = useState(row.can_impersonate)
  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState('')

  const needsDbName = role && !['vp', 'admin'].includes(role)

  const handleSave = async () => {
    if (!role) { setError('Please select a Brainwave role.'); return }
    if (needsDbName && !dbName.trim()) { setError('Name in Database is required for this role.'); return }
    setSaving(true); setError('')
    try {
      const payload: ProfileUpsertPayload = {
        user_email:      row.email,
        brainwave_role:  role,
        db_name:         dbName.trim() || null,
        qualifier_id:    qualifierId.trim() ? parseInt(qualifierId, 10) : null,
        can_impersonate: canImpersonate,
      }
      await brainwaveApi.upsertProfile(payload)
      onSave({ ...row, brainwave_role: role, db_name: dbName.trim() || null,
               qualifier_id: qualifierId.trim() ? parseInt(qualifierId, 10) : null,
               can_impersonate: canImpersonate, has_profile: true })
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to save. Please try again.')
    } finally { setSaving(false) }
  }

  const isPending = row.visually_role === 'pending'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(15,23,42,0.45)' }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-gray-100">
          <div>
            <h3 className="text-base font-semibold text-gray-900">Configure Access</h3>
            <p className="text-sm text-gray-500 mt-0.5 truncate max-w-xs">{row.full_name} · {row.email}</p>
            <span className={`inline-block mt-1 text-xs px-2 py-0.5 rounded-full ${
              isPending        ? 'bg-gray-100 text-gray-500' :
              row.visually_role === 'end_user' ? 'bg-purple-100 text-purple-700' : 'bg-blue-100 text-blue-700'
            }`}>
              {isPending ? 'Not yet signed up' : row.visually_role === 'end_user' ? 'Analyst on Visually' : 'Builder on Visually'}
            </span>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100 -mr-1">
            <X size={16} />
          </button>
        </div>

        <div className="px-6 py-5">
          <ProfileFields
            role={role} setRole={setRole}
            dbName={dbName} setDbName={setDbName}
            qualifierId={qualifierId} setQualifierId={setQualifierId}
            canImpersonate={canImpersonate} setCanImpersonate={setCanImpersonate}
          />
          {error && (
            <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2.5 mt-4">
              <AlertCircle size={14} className="flex-shrink-0" />{error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-6 pb-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors">
            Cancel
          </button>
          <button onClick={handleSave} disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-60">
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            {saving ? 'Saving…' : 'Save Access'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Pre-register modal (user not yet on Visually) ────────────────────────────

function PreRegisterModal({
  onSave, onClose,
}: { onSave: (row: BrainwaveUserRow) => void; onClose: () => void }) {
  const [email,          setEmail]          = useState('')
  const [role,           setRole]           = useState('')
  const [dbName,         setDbName]         = useState('')
  const [qualifierId,    setQualifierId]    = useState('')
  const [canImpersonate, setCanImpersonate] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState('')

  const needsDbName = role && !['vp', 'admin'].includes(role)

  const handleSave = async () => {
    const trimmedEmail = email.trim().toLowerCase()
    if (!trimmedEmail || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) {
      setError('Enter a valid email address.'); return
    }
    if (!role) { setError('Please select a Brainwave role.'); return }
    if (needsDbName && !dbName.trim()) { setError('Name in Database is required for this role.'); return }

    setSaving(true); setError('')
    try {
      const payload: ProfileUpsertPayload = {
        user_email:      trimmedEmail,
        brainwave_role:  role,
        db_name:         dbName.trim() || null,
        qualifier_id:    qualifierId.trim() ? parseInt(qualifierId, 10) : null,
        can_impersonate: canImpersonate,
      }
      await brainwaveApi.upsertProfile(payload)
      onSave({
        user_id:         trimmedEmail,   // synthetic key (no Visually account yet)
        email:           trimmedEmail,
        full_name:       trimmedEmail,
        visually_role:   'pending',
        brainwave_role:  role,
        db_name:         dbName.trim() || null,
        qualifier_id:    qualifierId.trim() ? parseInt(qualifierId, 10) : null,
        can_impersonate: canImpersonate,
        has_profile:     true,
      })
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to save. Please try again.')
    } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(15,23,42,0.45)' }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-gray-100">
          <div>
            <h3 className="text-base font-semibold text-gray-900">Pre-register Access</h3>
            <p className="text-sm text-gray-500 mt-0.5">
              Grant access to someone before they sign up. Their role will be ready the moment they create an account.
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100 -mr-1">
            <X size={16} />
          </button>
        </div>

        <div className="px-6 py-5 space-y-4">
          {/* Email */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Email Address <span className="text-red-400">*</span>
            </label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="e.g. analyst@brainwave.com"
              className="w-full border border-gray-200 rounded-lg px-3 py-2.5 text-sm placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500" />
            <p className="text-xs text-gray-400 mt-1">
              Must match the email they'll use to sign up for Visually.
            </p>
          </div>

          <ProfileFields
            role={role} setRole={setRole}
            dbName={dbName} setDbName={setDbName}
            qualifierId={qualifierId} setQualifierId={setQualifierId}
            canImpersonate={canImpersonate} setCanImpersonate={setCanImpersonate}
          />

          {error && (
            <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2.5">
              <AlertCircle size={14} className="flex-shrink-0" />{error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-6 pb-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors">
            Cancel
          </button>
          <button onClick={handleSave} disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-60">
            {saving ? <Loader2 size={13} className="animate-spin" /> : <UserPlus size={13} />}
            {saving ? 'Saving…' : 'Pre-register'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── User row ─────────────────────────────────────────────────────────────────

function UserRow({ row, onEdit }: { row: BrainwaveUserRow; onEdit: (r: BrainwaveUserRow) => void }) {
  const roleLabel = BRAINWAVE_ROLES.find(r => r.value === row.brainwave_role)?.label
  const roleColor = row.brainwave_role ? ROLE_COLORS[row.brainwave_role] : ''
  const initials  = (row.full_name || row.email).slice(0, 2).toUpperCase()
  const isAnalyst = row.visually_role === 'end_user'
  const isPending = row.visually_role === 'pending'

  return (
    <div className="flex items-center gap-4 px-5 py-4 hover:bg-gray-50 transition-colors group">
      {/* Avatar */}
      <div className={`w-9 h-9 rounded-full text-white text-sm font-semibold flex items-center justify-center flex-shrink-0 select-none ${
        isPending  ? 'bg-gradient-to-br from-gray-400 to-gray-500' :
        isAnalyst  ? 'bg-gradient-to-br from-purple-500 to-indigo-600' :
                     'bg-gradient-to-br from-blue-500 to-indigo-600'
      }`}>
        {isPending ? <Clock size={14} /> : initials}
      </div>

      {/* Name + email */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <p className="text-sm font-medium text-gray-900 truncate">
            {isPending ? row.email : row.full_name}
          </p>
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium flex-shrink-0 ${
            isPending  ? 'bg-gray-100 text-gray-500' :
            isAnalyst  ? 'bg-purple-100 text-purple-700' :
                         'bg-blue-100 text-blue-700'
          }`}>
            {isPending ? 'Pending sign-up' : isAnalyst ? 'Analyst' : 'Builder'}
          </span>
        </div>
        {!isPending && <p className="text-xs text-gray-400 truncate">{row.email}</p>}
      </div>

      {/* Brainwave profile status */}
      {row.has_profile ? (
        <div className="flex items-center gap-2 flex-shrink-0">
          {roleLabel && <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${roleColor}`}>{roleLabel}</span>}
          {row.db_name && <span className="text-xs text-gray-500 hidden md:inline truncate max-w-[120px]">{row.db_name}</span>}
          {row.can_impersonate && <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">Admin</span>}
          <UserCheck size={14} className="text-green-500 flex-shrink-0" />
        </div>
      ) : (
        <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0">
          <UserX size={14} />
          <span className="hidden sm:inline">No access</span>
        </div>
      )}

      {/* Configure button — visible on hover */}
      <button onClick={() => onEdit(row)}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-50 rounded-lg transition-colors flex-shrink-0 opacity-0 group-hover:opacity-100">
        <Edit3 size={12} />
        Configure
      </button>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function TeamAccessPage() {
  const [users,         setUsers]         = useState<BrainwaveUserRow[]>([])
  const [loading,       setLoading]       = useState(true)
  const [error,         setError]         = useState('')
  const [editRow,       setEditRow]       = useState<BrainwaveUserRow | null>(null)
  const [showPreReg,    setShowPreReg]    = useState(false)
  const [search,        setSearch]        = useState('')

  const activeUsers  = users.filter(u => u.visually_role !== 'pending')
  const pendingUsers = users.filter(u => u.visually_role === 'pending')
  const configured   = users.filter(u => u.has_profile).length

  useEffect(() => {
    brainwaveApi.listUsers()
      .then(r => setUsers(r.data))
      .catch(e => {
        const detail = e?.response?.data?.detail
        setError(e?.response?.status === 403
          ? 'You need admin access to manage team members. Ask an existing admin to grant you access.'
          : (detail ?? 'Failed to load users.'))
      })
      .finally(() => setLoading(false))
  }, [])

  const filterRow = (u: BrainwaveUserRow) =>
    !search ||
    u.full_name.toLowerCase().includes(search.toLowerCase()) ||
    u.email.toLowerCase().includes(search.toLowerCase())

  const filteredActive  = activeUsers.filter(filterRow)
  const filteredPending = pendingUsers.filter(filterRow)

  const handleSave = (updated: BrainwaveUserRow) => {
    setUsers(prev => {
      const idx = prev.findIndex(u => u.user_id === updated.user_id || u.email === updated.email)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = updated
        return next
      }
      return [...prev, updated]
    })
    setEditRow(null)
  }

  const handlePreRegSave = (row: BrainwaveUserRow) => {
    setUsers(prev => {
      const exists = prev.findIndex(u => u.email === row.email)
      if (exists >= 0) { const n = [...prev]; n[exists] = row; return n }
      return [...prev, row]
    })
    setShowPreReg(false)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="px-6 py-4 border-b border-gray-100 bg-white flex-shrink-0">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold font-display text-gray-900 flex items-center gap-2">
              <Users size={18} className="text-blue-600" />
              Team Access
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">
              Grant Brainwave data access to any Visually user — builders and analysts alike.
            </p>
          </div>
          <div className="flex items-center gap-3 flex-shrink-0">
            {!loading && !error && (
              <div className="text-right hidden sm:block">
                <p className="text-2xl font-bold text-gray-900">{configured}<span className="text-lg text-gray-400">/{users.length}</span></p>
                <p className="text-xs text-gray-400">configured</p>
              </div>
            )}
            <button onClick={() => setShowPreReg(true)}
              className="flex items-center gap-2 px-3.5 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-xl transition-colors">
              <UserPlus size={14} />
              Pre-register
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="max-w-3xl mx-auto space-y-4">

          {/* Info banner */}
          <div className="flex items-start gap-3 p-4 rounded-xl border border-blue-100 bg-blue-50">
            <Shield size={15} className="text-blue-500 mt-0.5 flex-shrink-0" />
            <p className="text-sm text-blue-700">
              <strong>Access restricted by default.</strong> Only users you configure with a Brainwave role
              can use the AI agents. Use <strong>Pre-register</strong> to grant access to someone before they sign up —
              their role will activate automatically when they create an account.
            </p>
          </div>

          {/* Loading */}
          {loading && (
            <div className="flex items-center justify-center py-16 text-gray-400">
              <Loader2 size={20} className="animate-spin mr-2" />Loading users…
            </div>
          )}

          {/* Error (including 403) */}
          {!loading && error && (
            <div className="flex items-start gap-3 p-4 rounded-xl border border-red-100 bg-red-50 text-sm text-red-700">
              <Lock size={15} className="flex-shrink-0 mt-0.5" />{error}
            </div>
          )}

          {/* User list */}
          {!loading && !error && (
            <>
              {/* Search */}
              <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                placeholder="Search by name or email…"
                className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500" />

              {/* Registered Visually users */}
              {filteredActive.length === 0 && !search ? (
                <div className="text-center py-12 text-gray-400">
                  <Users size={28} className="mx-auto mb-2 opacity-30" />
                  <p className="text-sm">No users registered yet.</p>
                </div>
              ) : filteredActive.length > 0 && (
                <div className="bg-white border border-gray-100 rounded-xl overflow-hidden divide-y divide-gray-50">
                  <div className="flex items-center gap-4 px-5 py-2.5 bg-gray-50 border-b border-gray-100">
                    <div className="w-9 flex-shrink-0" />
                    <p className="flex-1 text-xs font-medium text-gray-500 uppercase tracking-wide">User</p>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wide flex-shrink-0">Brainwave Access</p>
                    <div className="w-24 flex-shrink-0" />
                  </div>
                  {filteredActive.map(u => <UserRow key={u.user_id || u.email} row={u} onEdit={setEditRow} />)}
                </div>
              )}

              {/* Pre-registered / pending sign-up */}
              {filteredPending.length > 0 && (
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <Clock size={13} className="text-gray-400" />
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
                      Pending Sign-up ({filteredPending.length})
                    </p>
                  </div>
                  <div className="bg-white border border-dashed border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-50">
                    {filteredPending.map(u => <UserRow key={u.email} row={u} onEdit={setEditRow} />)}
                  </div>
                  <p className="text-xs text-gray-400 mt-1.5 ml-1">
                    These users have been pre-registered but haven't created a Visually account yet.
                    Their access activates automatically on sign-up.
                  </p>
                </div>
              )}

              {search && filteredActive.length === 0 && filteredPending.length === 0 && (
                <div className="text-center py-10 text-gray-400">
                  <Users size={24} className="mx-auto mb-2 opacity-30" />
                  <p className="text-sm">No users match "{search}".</p>
                </div>
              )}

              {/* Role reference */}
              {users.length > 0 && (
                <details className="group">
                  <summary className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none hover:text-gray-600 list-none">
                    <ChevronDown size={13} className="group-open:rotate-180 transition-transform" />
                    Role reference — SQL filter each role applies
                  </summary>
                  <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {[
                      { role: 'qualifying_specialist', label: 'Qualifying Specialist', filter: "WHERE qualifiername = 'name'  OR  qualifierid = id" },
                      { role: 'client_advisor',        label: 'Client Advisor',        filter: "WHERE clientadvisor = 'name'" },
                      { role: 'placement_specialist',  label: 'Placement Specialist',  filter: "WHERE placementspecialist = 'name'" },
                      { role: 'relationship_manager',  label: 'Relationship Manager',  filter: "WHERE relationshipmanager = 'name'" },
                      { role: 'vp',                    label: 'VP',                    filter: 'No filter — sees all data' },
                      { role: 'admin',                 label: 'Administrator',         filter: 'No filter — sees all + can impersonate' },
                    ].map(r => (
                      <div key={r.role} className="flex items-start gap-2 p-3 rounded-lg border border-gray-100 bg-white">
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0 ${ROLE_COLORS[r.role]}`}>{r.label}</span>
                        <code className="text-xs text-gray-500 font-mono leading-snug break-all">{r.filter}</code>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      </div>

      {editRow    && <EditModal      row={editRow} onSave={handleSave}      onClose={() => setEditRow(null)} />}
      {showPreReg && <PreRegisterModal             onSave={handlePreRegSave} onClose={() => setShowPreReg(false)} />}
    </div>
  )
}
