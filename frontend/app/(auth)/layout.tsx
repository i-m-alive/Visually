export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-light via-white to-accent-light p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-brand font-display">Visually</h1>
          <p className="text-gray-500 mt-1 text-sm">AI-powered data visualization</p>
        </div>
        <div className="card p-8">
          {children}
        </div>
      </div>
    </div>
  )
}
