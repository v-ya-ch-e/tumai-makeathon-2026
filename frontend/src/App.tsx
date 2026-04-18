import { useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import {
  Button,
  Card,
  Chip,
  Drawer,
  Input,
  ProgressSteps,
  Select,
  StatusPill,
  Textarea,
} from './components/ui'
import HealthPage from './pages/Health'

function DesignShowcase() {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [chipA, setChipA] = useState(false)
  const [chipB, setChipB] = useState(true)

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-6 py-12">
      <ProgressSteps current={2} />

      <Card className="space-y-4">
        <Input placeholder="Input" aria-label="Demo input" />
        <Textarea placeholder="Textarea" aria-label="Demo textarea" />
        <Select aria-label="Demo select" defaultValue="a">
          <option value="a">Option A</option>
          <option value="b">Option B</option>
          <option value="c">Option C</option>
        </Select>
        <div className="flex flex-wrap gap-2">
          <Chip selected={chipA} onToggle={() => setChipA((v) => !v)}>
            Chip A
          </Chip>
          <Chip selected={chipB} onToggle={() => setChipB((v) => !v)}>
            Chip B
          </Chip>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone="idle">Idle</StatusPill>
          <StatusPill tone="running">Running</StatusPill>
          <StatusPill tone="rescanning">Rescanning</StatusPill>
          <StatusPill tone="good">Good</StatusPill>
          <StatusPill tone="warn">Warn</StatusPill>
          <StatusPill tone="bad">Bad</StatusPill>
        </div>
        <Button type="button" variant="primary" onClick={() => setDrawerOpen(true)}>
          Open drawer
        </Button>
      </Card>

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="Drawer"
      >
        <p className="text-[15px] text-ink">
          Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut
          labore et dolore magna aliqua.
        </p>
      </Drawer>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<DesignShowcase />} />
        <Route path="/health" element={<HealthPage />} />
      </Routes>
    </BrowserRouter>
  )
}
