import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'

import { HoloVoiceConsole } from './voice-activity'

describe('HoloVoiceConsole', () => {
  afterEach(() => cleanup())

  it('renders Jarvis-style governed voice state in Chinese', () => {
    const { container } = render(
      <I18nProvider configClient={null} initialLocale="zh">
        <HoloVoiceConsole level={0.72} muted={false} status="listening" />
      </I18nProvider>
    )

    expect(screen.getByRole('status', { name: 'JARVIS 模式: 聆听中' })).toBeTruthy()
    expect(screen.getByText('反应堆在线')).toBeTruthy()
    expect(container.textContent).toContain('自动重新开麦')
    expect(container.textContent).toContain('工具动作仍走 Hermes 安全门禁')
    expect(container.querySelector('[data-slot="holo-voice-console"]')).toBeTruthy()
  })

  it('shows muted state over the underlying conversation state', () => {
    render(
      <I18nProvider configClient={null} initialLocale="en">
        <HoloVoiceConsole level={0.25} muted status="speaking" />
      </I18nProvider>
    )

    expect(screen.getByRole('status', { name: 'JARVIS MODE: Mic muted' })).toBeTruthy()
  })
})
