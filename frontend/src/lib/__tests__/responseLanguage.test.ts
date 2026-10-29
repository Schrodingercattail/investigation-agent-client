import { describe, expect, it } from 'vitest'
import {
  detectResponseLanguage,
  investigatingEventText,
  investigatingText,
} from '../responseLanguage'

describe('detectResponseLanguage (client narrative mirror)', () => {
  it('English request → en', () => {
    expect(detectResponseLanguage('please explain what ML detected'))
      .toBe('en')
  })

  it('Chinese request → zh', () => {
    expect(detectResponseLanguage('请解释机器学习检测到了什么'))
      .toBe('zh')
  })

  it('Chinese request with technical terms stays zh', () => {
    expect(detectResponseLanguage('解释一下 ML 信号分数和 LightGBM 特征'))
      .toBe('zh')
  })

  it('English with a stray CJK token stays en', () => {
    expect(detectResponseLanguage('explain finding 对冲 for me'))
      .toBe('en')
  })

  it('pure id / empty input → en', () => {
    expect(detectResponseLanguage('U00047')).toBe('en')
    expect(detectResponseLanguage('')).toBe('en')
    expect(detectResponseLanguage(null)).toBe('en')
  })
})

describe('selection-confirmation narratives', () => {
  it('renders both faces with the label verbatim', () => {
    expect(investigatingText('en', 'F1 — ML Pattern Detection'))
      .toBe("You're now investigating F1 — ML Pattern Detection.")
    expect(investigatingText('zh', 'F1 — ML Pattern Detection'))
      .toBe('正在调查 F1 — ML Pattern Detection。')
    expect(investigatingEventText('zh', 'E3', 'F1 — High withdrawal'))
      .toBe('正在调查 F1 — High withdrawal 中的事件 E3。')
    expect(investigatingEventText('en', 'E3', 'F1 — High withdrawal'))
      .toBe("You're now investigating event E3 of F1 — High withdrawal.")
  })
})
