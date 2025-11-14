import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import jQuery from 'jquery';
import './index.css'
import App from './App.tsx'

(window as any).$ = jQuery;
(window as any).jQuery = jQuery;

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)

