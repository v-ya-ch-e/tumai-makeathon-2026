import { BrowserRouter, Route, Routes } from 'react-router-dom'
import HealthPage from './pages/Health'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={
            <p className="p-4 font-sans">WG Hunter — frontend scaffold ready</p>
          }
        />
        <Route path="/health" element={<HealthPage />} />
      </Routes>
    </BrowserRouter>
  )
}
