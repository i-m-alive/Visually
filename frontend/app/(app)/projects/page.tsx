'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { projectApi } from '@/lib/api'
import { Plus, Database, Trash2 } from 'lucide-react'

interface Project {
  id: string
  name: string
  description?: string
  created_at: string
}

export default function ProjectsPage() {
  const router = useRouter()
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  useEffect(() => {
    projectApi.list().then((r) => {
      setProjects(r.data)
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (confirmId !== id) {
      setConfirmId(id)
      return
    }
    setDeletingId(id)
    setConfirmId(null)
    try {
      await projectApi.delete(id)
      setProjects(prev => prev.filter(p => p.id !== id))
    } catch {
      // ignore
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="flex-1 p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold font-display text-gray-900">Projects</h1>
        <Link href="/projects/new" className="btn-primary flex items-center gap-2">
          <Plus size={16} /> New project
        </Link>
      </div>

      {loading ? (
        <div className="text-gray-500 text-sm">Loading...</div>
      ) : projects.length === 0 ? (
        <div className="text-center py-16">
          <Database size={48} className="mx-auto text-gray-300 mb-4" />
          <h3 className="text-lg font-semibold text-gray-600 mb-2">No projects yet</h3>
          <p className="text-gray-400 text-sm mb-4">Connect a database to get started</p>
          <Link href="/projects/new" className="btn-primary inline-flex items-center gap-2">
            <Plus size={16} /> Create project
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map((p) => (
            <div
              key={p.id}
              onClick={() => router.push(`/projects/${p.id}/query`)}
              className="card p-4 cursor-pointer hover:shadow-md transition-shadow relative group"
            >
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-lg bg-brand-light flex items-center justify-center shrink-0">
                  <Database size={20} className="text-brand" />
                </div>
                <div className="min-w-0 flex-1">
                  <h3 className="font-semibold text-gray-900 truncate">{p.name}</h3>
                  {p.description && <p className="text-sm text-gray-500 mt-0.5 truncate">{p.description}</p>}
                  <p className="text-xs text-gray-400 mt-1">
                    {new Date(p.created_at).toLocaleDateString()}
                  </p>
                </div>
              </div>

              {/* Delete button */}
              <div className="absolute top-3 right-3" onClick={(e) => e.stopPropagation()}>
                {confirmId === p.id ? (
                  <div className="flex items-center gap-1.5 bg-white border border-red-200 rounded-lg px-2 py-1 shadow-sm">
                    <span className="text-xs text-red-600 font-medium">Delete?</span>
                    <button
                      onClick={(e) => handleDelete(p.id, e)}
                      disabled={deletingId === p.id}
                      className="text-xs font-semibold text-white bg-red-500 hover:bg-red-600 px-2 py-0.5 rounded transition-colors"
                    >
                      Yes
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); setConfirmId(null) }}
                      className="text-xs text-gray-500 hover:text-gray-700"
                    >
                      No
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={(e) => handleDelete(p.id, e)}
                    className="opacity-0 group-hover:opacity-100 p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all"
                    title="Delete project"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
