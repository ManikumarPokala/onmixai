import { useState, useRef, type FormEvent } from 'react'
import { ConsequenceConfirm } from '../../components/ConsequenceConfirm'
import { useToast } from '../../lib/toast/useToast'
import {
  useCollections,
  useCreateCollection,
  useDeleteCollection,
  useDocuments,
  useUploadDocument,
  useUploadDocumentVersion,
  useReindexDocument,
  useDeleteDocument,
} from './api'
import type { CollectionResponse, DocumentResponse } from '../../lib/api'

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

export function DocumentsPage() {
  const toast = useToast()

  // Queries
  const { data: collections = [], isLoading: loadColPending, isError: loadColError } = useCollections()
  const [selectedId, setSelectedId] = useState<string>('')
  const { data: documents = [], isLoading: loadDocsPending, isError: loadDocsError } = useDocuments(selectedId)

  // Mutations
  const createCollection = useCreateCollection()
  const deleteCollection = useDeleteCollection()
  const uploadDoc = useUploadDocument(selectedId)
  const uploadVersion = useUploadDocumentVersion(selectedId)
  const reindexDoc = useReindexDocument(selectedId)
  const deleteDoc = useDeleteDocument(selectedId)

  // State
  const [newColName, setNewColName] = useState('')
  const [newColDesc, setNewColDesc] = useState('')
  const [colToDelete, setColToDelete] = useState<CollectionResponse | null>(null)
  const [docToDelete, setDocToDelete] = useState<DocumentResponse | null>(null)
  
  // For file selection
  const uploadInputRef = useRef<HTMLInputElement>(null)
  const [versioningDoc, setVersioningDoc] = useState<string | null>(null)
  const versionInputRef = useRef<HTMLInputElement>(null)

  const selectedCol = collections.find((c) => c.id === selectedId)

  function handleCreateCollection(e: FormEvent) {
    e.preventDefault()
    const name = newColName.trim()
    if (!name) return
    createCollection.mutate(
      { name, description: newColDesc.trim() || null },
      {
        onSuccess: (data) => {
          toast.notify('Collection created.', 'success')
          setNewColName('')
          setNewColDesc('')
          setSelectedId(data.id)
        },
        onError: toast.notifyError,
      }
    )
  }

  function handleDeleteCollection() {
    if (!colToDelete) return
    deleteCollection.mutate(colToDelete.id, {
      onSuccess: () => {
        toast.notify('Collection deleted.', 'success')
        if (selectedId === colToDelete.id) {
          setSelectedId('')
        }
        setColToDelete(null)
      },
      onError: (err) => {
        // If there's an error like COLLECTION_NOT_EMPTY, the global handler works.
        toast.notifyError(err)
        setColToDelete(null)
      },
    })
  }

  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file || !selectedId) return
    uploadDoc.mutate(
      { file },
      {
        onSuccess: () => {
          toast.notify('Document upload started.', 'success')
          if (uploadInputRef.current) uploadInputRef.current.value = ''
        },
        onError: toast.notifyError,
      }
    )
  }

  function handleVersionUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file || !versioningDoc) return
    uploadVersion.mutate(
      { documentId: versioningDoc, file },
      {
        onSuccess: () => {
          toast.notify('New version upload started.', 'success')
          setVersioningDoc(null)
          if (versionInputRef.current) versionInputRef.current.value = ''
        },
        onError: (err) => {
          toast.notifyError(err)
          setVersioningDoc(null)
        },
      }
    )
  }

  function handleReindex(docId: string) {
    reindexDoc.mutate(docId, {
      onSuccess: () => toast.notify('Reindexing started.', 'success'),
      onError: toast.notifyError,
    })
  }

  function handleDeleteDoc() {
    if (!docToDelete) return
    deleteDoc.mutate(docToDelete.id, {
      onSuccess: () => {
        toast.notify('Document deleted.', 'success')
        setDocToDelete(null)
      },
      onError: toast.notifyError,
    })
  }

  return (
    <div className="documents-layout">
      {/* Sidebar: Collections list & create form */}
      <aside className="collection-sidebar" aria-label="Collections Sidebar">
        <div className="session-list__head">
          <h2>Collections</h2>
        </div>

        {loadColPending && <p style={{ padding: '1rem' }}>Loading collections…</p>}
        {loadColError && (
          <p role="alert" className="form-error" style={{ margin: '1rem' }}>
            Could not load collections.
          </p>
        )}

        <ul className="session-list__items">
          {collections.map((col) => (
            <li
              key={col.id}
              className={`session-item ${selectedId === col.id ? 'is-active' : ''}`}
            >
              <button
                type="button"
                className="session-item__open"
                onClick={() => setSelectedId(col.id)}
              >
                {col.name}
              </button>
              <button
                type="button"
                className="session-item__menu"
                title="Delete Collection"
                aria-label={`Delete collection ${col.name}`}
                onClick={(e) => {
                  e.stopPropagation()
                  setColToDelete(col)
                }}
              >
                🗑
              </button>
            </li>
          ))}
          {!loadColPending && collections.length === 0 && (
            <p className="empty-state" style={{ padding: '1rem' }}>
              No collections yet. Create one below to start uploading documents.
            </p>
          )}
        </ul>

        {/* Create collection form */}
        <form className="col-create-form" onSubmit={handleCreateCollection}>
          <h3>New Collection</h3>
          <label className="field">
            <span>Name</span>
            <input
              type="text"
              required
              value={newColName}
              onChange={(e) => setNewColName(e.target.value)}
              placeholder="e.g. Standard Procedures"
            />
          </label>
          <label className="field">
            <span>Description (optional)</span>
            <input
              type="text"
              value={newColDesc}
              onChange={(e) => setNewColDesc(e.target.value)}
              placeholder="e.g. Frictional SOP manuals"
            />
          </label>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={createCollection.isPending || !newColName.trim()}
          >
            {createCollection.isPending ? 'Creating…' : 'Create'}
          </button>
        </form>
      </aside>

      {/* Main Area: Documents management */}
      <main className="documents-pane" aria-label="Documents Management">
        {selectedCol ? (
          <div>
            <div className="doc-pane-header">
              <h1>{selectedCol.name}</h1>
              {selectedCol.description && (
                <p className="doc-pane-desc">{selectedCol.description}</p>
              )}
            </div>

            {/* Upload Area */}
            <div
              className="upload-zone"
              onClick={() => uploadInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') uploadInputRef.current?.click()
              }}
              role="button"
              tabIndex={0}
              aria-label="Upload document to collection"
            >
              <input
                type="file"
                ref={uploadInputRef}
                style={{ display: 'none' }}
                onChange={handleFileUpload}
                accept=".pdf,.docx,.pptx,.xlsx,.txt"
              />
              <p>
                {uploadDoc.isPending
                  ? 'Uploading document…'
                  : 'Click or drop a file here to upload (PDF, DOCX, PPTX, XLSX, TXT)'}
              </p>
              {uploadDoc.isPending && <div className="skeleton" style={{ width: '50%', margin: '0.5rem auto 0' }} />}
            </div>

            {/* Invisible file input for uploading new versions */}
            <input
              type="file"
              ref={versionInputRef}
              style={{ display: 'none' }}
              onChange={handleVersionUpload}
              accept=".pdf,.docx,.pptx,.xlsx,.txt"
            />

            {/* Document list */}
            <h2>Documents</h2>
            {loadDocsPending && <p>Loading documents…</p>}
            {loadDocsError && (
              <p role="alert" className="form-error">
                Could not load documents for this collection.
              </p>
            )}

            {!loadDocsPending && documents.length === 0 && (
              <div className="empty-state">
                <p>This collection has no documents. Upload one above to get started.</p>
              </div>
            )}

            {!loadDocsPending && documents.length > 0 && (
              <table className="doc-table">
                <thead>
                  <tr>
                    <th>Filename</th>
                    <th>Version</th>
                    <th>Size</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {documents.map((doc) => (
                    <tr key={doc.id} className="doc-row">
                      <td className="doc-filename">
                        <span title={doc.filename}>{doc.filename}</span>
                        {doc.superseded && <span className="superseded-tag">Superseded</span>}
                      </td>
                      <td>v{doc.version}</td>
                      <td>{formatBytes(doc.size_bytes)}</td>
                      <td>
                        <span
                          className={`status-badge status-badge--${doc.status}`}
                          title={doc.status === 'failed' ? doc.failure_reason || 'Unknown error' : undefined}
                        >
                          {doc.status}
                        </span>
                        {doc.status === 'failed' && doc.failure_reason && (
                          <div className="failed-reason">{doc.failure_reason}</div>
                        )}
                      </td>
                      <td>
                        <div className="doc-actions">
                          <button
                            type="button"
                            className="btn btn--ghost btn--small"
                            title="Upload new version"
                            onClick={() => {
                              setVersioningDoc(doc.id)
                              setTimeout(() => versionInputRef.current?.click(), 0)
                            }}
                            disabled={doc.status === 'processing' || uploadVersion.isPending}
                          >
                            ⬆ Version
                          </button>
                          <button
                            type="button"
                            className="btn btn--ghost btn--small"
                            title="Reindex document chunks"
                            onClick={() => handleReindex(doc.id)}
                            disabled={doc.status !== 'ready' || reindexDoc.isPending}
                          >
                            🔄 Reindex
                          </button>
                          <button
                            type="button"
                            className="btn btn--ghost btn--small btn--danger-ghost"
                            title="Delete document"
                            onClick={() => setDocToDelete(doc)}
                            disabled={doc.status === 'processing' || deleteDoc.isPending}
                          >
                            🗑 Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ) : (
          <div className="empty-state" style={{ margin: 'auto' }}>
            <h1>Select a collection</h1>
            <p>Choose a collection from the sidebar, or create a new one to manage your documents.</p>
          </div>
        )}
      </main>

      {/* Confirmation Modals */}
      <ConsequenceConfirm
        open={colToDelete !== null}
        title="Delete collection?"
        message={`Are you sure you want to delete the collection "${colToDelete?.name}"? Non-empty collections cannot be deleted until all documents are removed.`}
        confirmLabel="Delete"
        onCancel={() => setColToDelete(null)}
        onConfirm={handleDeleteCollection}
      />

      <ConsequenceConfirm
        open={docToDelete !== null}
        title="Delete document?"
        message={`Are you sure you want to delete "${docToDelete?.filename}"? This action will permanently purge all chunks, embeddings, and object storage files for this document.`}
        confirmLabel="Delete"
        onCancel={() => setDocToDelete(null)}
        onConfirm={handleDeleteDoc}
      />
    </div>
  )
}
