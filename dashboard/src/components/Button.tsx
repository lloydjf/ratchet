import { forwardRef, type ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-text text-bg border-text hover:bg-[color-mix(in_srgb,var(--text)_88%,var(--bg))] font-medium',
  secondary: 'bg-surface text-text border-border-strong hover:bg-surface-2',
  danger: 'bg-surface text-block-ink border-[color-mix(in_srgb,var(--block)_70%,var(--surface))] hover:bg-[color-mix(in_srgb,var(--block)_10%,var(--surface))] font-medium',
  ghost: 'bg-transparent text-text border-transparent hover:bg-surface-2',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'md' | 'sm'
}

/** Text-labelled button, 6-px radius; md is 36 px tall, sm 32 px (touch layouts add their own hit area). */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', className = '', type = 'button', ...rest },
  ref,
) {
  const s = size === 'sm' ? 'min-h-8 px-3 text-body' : 'min-h-9 px-3.5 text-body'
  return (
    <button
      ref={ref}
      type={type}
      className={`inline-flex items-center justify-center gap-2 rounded-sm border whitespace-nowrap transition-[background-color,border-color] duration-[160ms] max-md:min-h-11 disabled:cursor-not-allowed disabled:opacity-50 ${s} ${VARIANT[variant]} ${className}`}
      {...rest}
    />
  )
})
