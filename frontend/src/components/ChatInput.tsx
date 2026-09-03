import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowUp, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ChatInputProps {
  onSubmit: (message: string) => void
  disabled?: boolean
  loading?: boolean
  placeholder?: string
  className?: string
}

export function ChatInput({
  onSubmit,
  disabled = false,
  loading = false,
  placeholder = 'Investigate a case, e.g. U00299',
  className,
}: ChatInputProps) {
  const [input, setInput] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (input.trim() && !disabled && !loading) {
      onSubmit(input.trim())
      setInput('')
    }
  }

  const isDisabled = disabled || loading || !input.trim()

  return (
    <div className={cn('w-full', className)}>
      <form onSubmit={handleSubmit}>
        <div className="flex items-end gap-2 bg-muted/50 rounded-2xl p-2 border border-border/50">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={placeholder}
            disabled={disabled || loading}
            className="flex-1 bg-transparent border-0 focus:outline-none focus:ring-0 resize-none text-sm px-2 py-1 min-h-[44px] max-h-[120px] placeholder:text-muted-foreground"
            rows={1}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !isDisabled) {
                e.preventDefault()
                handleSubmit(e)
              }
            }}
          />
          <Button
            type="submit"
            disabled={isDisabled}
            size="icon"
            className="shrink-0 h-8 w-8 rounded-full"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ArrowUp className="h-4 w-4" />
            )}
          </Button>
        </div>
      </form>
    </div>
  )
}
