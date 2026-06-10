'use client'
import React, { useState, useEffect, useCallback } from 'react'
import {
  X, Link2, Copy, Check, Trash2, Plus, Users, Globe, Eye,
  Loader2, RefreshCw, Shield, Clock, Mail,
} from 'lucide-react'
import { shareApi } from '@/lib/api'

interface ShareToken {
  id: string
  mode: string
  label: string | null
  access_count: number
  last_used_at: string | null
  expires_at: string | null
  created_at: string
}

interface Collaborator {
  id: string
  user_id: string
  email: string
  full_name: string
  role: string
  created_at: string
}

interface Props {
  canvasId: string
  canvasName: string
  onClose: () => void
}

type Tab = 'links' | 'collaborators'

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1800) }}
      className="p-1.5 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors flex-shrink-0"
      title="Copy"
    >
      {copied ? <Check size={13} className="text-green-500" /> : <Copy size={13} />}
    </button>
  )
}

export function ShareModal({ canvasId, canvasName, onClose }: Props) {
  const [tab, setTab] = useState<Tab>('links')
  const [shares, setShares] = useState<ShareToken[]>([])
  const [collabs, setCollabs] = useState<Collaborator[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newMode, setNewMode] = useState<'live' | 'snapshot'>('live')
  const [newLabel, setNewLabel] = useState('')
  const [expiryDays, setExpiryDays] = useState<string>('30')
  const [newToken, setNewToken] = useState<{ share_url: string; embed_url: string; token: string } | null>(null)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<'viewer' | 'editor'>('viewer')
  const [inviting, setInviting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [sharesResp, collabsResp] = await Promise.all([
        shareApi.list(canvasId),
        shareApi.listCollaborators(canvasId),
      ])
      setShares(sharesResp.data.shares ?? [])
      setCollabs(collabsResp.data.collaborators ?? [])
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [canvasId])

  useEffect(() => { loadAll() }, [loadAll])

  const handleCreateLink = async () => {
    setCreating(true)
    setError(null)
    setNewToken(null)
    try {
      const resp = await shareApi.create(canvasId, {
        mode: newMode,
        label: newLabel.trim() || undefined,
        expires_days: expiryDays === 'never' ? null : parseInt(expiryDays, 10),
      })
      setNewToken({
        share_url: resp.data.share_url,
        embed_url: resp.data.embed_url,
        token: resp.data.token,
      })
      setNewLabel('')
      await loadAll()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg || 'Failed to create share link')
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async (tokenId: string) => {
    try {
      await shareApi.revoke(canvasId, tokenId)
      setShares(prev => prev.filter(s => s.id !== tokenId))
    } catch { /* ignore */ }
  }

  const handleInvite = async () => {
    if (!inviteEmail.trim()) return
    setInviting(true)
    setError(null)
    try {
      const resp = await shareApi.addCollaborator(canvasId, { email: inviteEmail.trim(), role: inviteRole })
      setCollabs(prev => {
        const exists = prev.find(c => c.user_id === resp.data.user_id)
        return exists ? prev.map(c => c.user_id === resp.data.user_id ? resp.data : c) : [...prev, resp.data]
      })
      setInviteEmail('')
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg || 'Failed to invite collaborator')
    } finally {
      setInviting(false)
    }
  }

  const handleRemoveCollab = async (userId: string) => {
    try {
      await shareApi.removeCollaborator(canvasId, userId)
      setCollabs(prev => prev.filter(c => c.user_id !== userId))
    } catch { /* ignore */ }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)', backdropFilter: 'blur(4px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 flex flex-col overflow-hidden" style={{ maxHeight: '90vh' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <div>
            <h2 className="text-sm font-semibold text-gray-900">Share "{canvasName}"</h2>
            <p className="text-xs text-gray-400 mt-0.5">Create share links or invite collaborators</p>
          </div>
          <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-100 flex-shrink-0">
          {(['links', 'collaborators'] as Tab[]).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className="flex-1 py-2.5 text-xs font-semibold transition-colors"
              style={{
                color: tab === t ? '#2563EB' : '#6B7280',
                borderBottom: `2px solid ${tab === t ? '#2563EB' : 'transparent'}`,
              }}
            >
              {t === 'links' ? <><Globe size={11} className="inline mr-1.5" />Share Links</> : <><Users size={11} className="inline mr-1.5" />Collaborators</>}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 size={20} className="animate-spin text-gray-300" />
            </div>
          ) : tab === 'links' ? (
            <div className="p-5 space-y-5">
              {/* Create new link */}
              <div className="border border-gray-200 rounded-xl p-4 space-y-3 bg-gray-50">
                <p className="text-xs font-semibold text-gray-700">Create new share link</p>

                {/* Mode */}
                <div className="flex gap-2">
                  {(['live', 'snapshot'] as const).map(m => (
                    <button
                      key={m}
                      onClick={() => setNewMode(m)}
                      className="flex-1 py-1.5 rounded-lg text-xs font-medium border transition-all"
                      style={{
                        background: newMode === m ? '#EFF6FF' : 'white',
                        borderColor: newMode === m ? '#BFDBFE' : '#E5E7EB',
                        color: newMode === m ? '#2563EB' : '#6B7280',
                      }}
                    >
                      {m === 'live' ? <><RefreshCw size={10} className="inline mr-1" />Live data</> : <><Shield size={10} className="inline mr-1" />Snapshot</>}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-gray-400">
                  {newMode === 'live'
                    ? 'Viewers see live data — your server proxies queries on their behalf. No DB credentials shared.'
                    : 'Viewers see a frozen copy of the data as it was when the link was created.'}
                </p>

                {/* Label + expiry */}
                <div className="flex gap-2">
                  <input
                    value={newLabel}
                    onChange={e => setNewLabel(e.target.value)}
                    placeholder="Label (optional)"
                    className="flex-1 px-3 py-1.5 text-xs border border-gray-200 rounded-lg outline-none focus:border-blue-400 bg-white"
                  />
                  <select
                    value={expiryDays}
                    onChange={e => setExpiryDays(e.target.value)}
                    className="px-2 py-1.5 text-xs border border-gray-200 rounded-lg outline-none bg-white text-gray-700"
                  >
                    <option value="7">7 days</option>
                    <option value="30">30 days</option>
                    <option value="90">90 days</option>
                    <option value="365">1 year</option>
                    <option value="never">Never</option>
                  </select>
                </div>

                <button
                  onClick={handleCreateLink}
                  disabled={creating}
                  className="w-full py-2 rounded-lg text-xs font-semibold text-white transition-opacity disabled:opacity-50 flex items-center justify-center gap-1.5"
                  style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
                >
                  {creating ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                  {creating ? 'Creating…' : 'Generate Share Link'}
                </button>
              </div>

              {/* Newly created token — show once */}
              {newToken && (
                <div className="border border-green-200 rounded-xl p-4 bg-green-50 space-y-2.5">
                  <p className="text-xs font-semibold text-green-700 flex items-center gap-1.5">
                    <Check size={12} />Link created — copy it now, it won&apos;t be shown again
                  </p>
                  <div className="space-y-1.5">
                    {[
                      { label: 'Share URL', url: newToken.share_url },
                      { label: 'Embed URL', url: newToken.embed_url },
                    ].map(({ label, url }) => (
                      <div key={label} className="flex items-center gap-2 bg-white rounded-lg px-3 py-1.5 border border-green-200">
                        <span className="text-xs text-gray-400 w-16 flex-shrink-0">{label}</span>
                        <span className="flex-1 text-xs text-gray-700 font-mono truncate">{url}</span>
                        <CopyButton text={url} />
                      </div>
                    ))}
                    <div className="mt-1.5 p-2 bg-gray-50 rounded-lg border border-gray-200">
                      <p className="text-xs font-medium text-gray-500 mb-1">Embed snippet</p>
                      <code className="text-xs text-gray-600 break-all">{`<iframe src="${newToken.embed_url}" width="1200" height="800" frameborder="0" style="border-radius:12px"></iframe>`}</code>
                    </div>
                  </div>
                </div>
              )}

              {error && <p className="text-xs text-red-500">{error}</p>}

              {/* Existing links */}
              {shares.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Active links ({shares.length})</p>
                  {shares.map(s => (
                    <div key={s.id} className="flex items-start gap-3 p-3 border border-gray-100 rounded-xl bg-white">
                      <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
                        style={{ background: s.mode === 'live' ? '#EFF6FF' : '#F0FDF4' }}>
                        {s.mode === 'live'
                          ? <RefreshCw size={12} className="text-blue-500" />
                          : <Shield size={12} className="text-green-500" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-gray-800">{s.label || `${s.mode === 'live' ? 'Live' : 'Snapshot'} link`}</span>
                          <span className="text-xs px-1.5 py-0.5 rounded-full" style={{ background: s.mode === 'live' ? '#DBEAFE' : '#DCFCE7', color: s.mode === 'live' ? '#1D4ED8' : '#15803D' }}>{s.mode}</span>
                        </div>
                        <div className="flex items-center gap-3 mt-0.5">
                          <span className="text-xs text-gray-400 flex items-center gap-1">
                            <Eye size={10} />{s.access_count} views
                          </span>
                          {s.expires_at && (
                            <span className="text-xs text-gray-400 flex items-center gap-1">
                              <Clock size={10} />Expires {new Date(s.expires_at).toLocaleDateString()}
                            </span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() => handleRevoke(s.id)}
                        className="p-1.5 text-gray-300 hover:text-red-500 rounded-lg transition-colors flex-shrink-0"
                        title="Revoke link"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="p-5 space-y-4">
              {/* Invite form */}
              <div className="border border-gray-200 rounded-xl p-4 bg-gray-50 space-y-3">
                <p className="text-xs font-semibold text-gray-700">Invite by email</p>
                <div className="flex gap-2">
                  <input
                    type="email"
                    value={inviteEmail}
                    onChange={e => setInviteEmail(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleInvite()}
                    placeholder="colleague@company.com"
                    className="flex-1 px-3 py-1.5 text-xs border border-gray-200 rounded-lg outline-none focus:border-blue-400 bg-white"
                  />
                  <select
                    value={inviteRole}
                    onChange={e => setInviteRole(e.target.value as 'viewer' | 'editor')}
                    className="px-2 py-1.5 text-xs border border-gray-200 rounded-lg outline-none bg-white text-gray-700"
                  >
                    <option value="viewer">Viewer</option>
                    <option value="editor">Editor</option>
                  </select>
                </div>
                <button
                  onClick={handleInvite}
                  disabled={inviting || !inviteEmail.trim()}
                  className="w-full py-2 rounded-lg text-xs font-semibold text-white transition-opacity disabled:opacity-50 flex items-center justify-center gap-1.5"
                  style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
                >
                  {inviting ? <Loader2 size={12} className="animate-spin" /> : <Mail size={12} />}
                  {inviting ? 'Inviting…' : 'Send Invite'}
                </button>
              </div>

              {error && <p className="text-xs text-red-500">{error}</p>}

              {/* Collaborators list */}
              {collabs.length > 0 ? (
                <div className="space-y-2">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{collabs.length} collaborator{collabs.length !== 1 ? 's' : ''}</p>
                  {collabs.map(c => (
                    <div key={c.id} className="flex items-center gap-3 p-3 border border-gray-100 rounded-xl bg-white">
                      <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                        style={{ background: 'linear-gradient(135deg, #6366F1, #8B5CF6)' }}>
                        {(c.full_name || c.email).charAt(0).toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-medium text-gray-800 truncate">{c.full_name || c.email}</p>
                        <p className="text-xs text-gray-400 truncate">{c.email}</p>
                      </div>
                      <span className="text-xs px-2 py-0.5 rounded-full flex-shrink-0"
                        style={{
                          background: c.role === 'editor' ? '#FEF3C7' : '#F3F4F6',
                          color: c.role === 'editor' ? '#92400E' : '#6B7280',
                        }}>
                        {c.role}
                      </span>
                      <button
                        onClick={() => handleRemoveCollab(c.user_id)}
                        className="p-1.5 text-gray-300 hover:text-red-500 rounded-lg transition-colors flex-shrink-0"
                        title="Remove"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-gray-400">
                  <Users size={24} className="mx-auto mb-2 opacity-30" />
                  <p className="text-xs">No collaborators yet</p>
                  <p className="text-xs mt-1 opacity-70">Invite team members above</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
