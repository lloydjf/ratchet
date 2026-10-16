import { forwardRef, type ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-bg border-accent hover:brightness-110 font-semibold',
  secondary: 'bg-surface text-text border-border-strong hover:bg-surface-2',
  danger: 'tint-block hover:brightness-110 font-semibold',
  ghost: 'bg-transparent text-text border-transparent hover:bg-surface-2',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'md' | 'sm'
}

/** Text-labelled button; touch target ≥ 44 px (sm keeps 44 px of hit area via min-height). */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', className = '', type = 'button', ...rest },
  ref,
) {
  const s = size === 'sm' ? 'min-h-11 px-3 text-meta' : 'min-h-11 px-4 text-body'
  return (
    <button
      ref={ref}
      type={type}
      className={`inline-flex items-center justify-center gap-2 rounded-sm border font-medium whitespace-nowrap transition-[filter,background-color] duration-[160ms] disabled:cursor-not-allowed disabled:opacity-50 ${s} ${VARIANT[variant]} ${className}`}
      {...rest}
    />
  )
})
