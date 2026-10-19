import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Common";
import Workbench from "./pages/Workbench";
const Review = lazy(() => import("./pages/Review"));
const Results = lazy(() => import("./pages/Results"));

export default function App() {
  return (
    <Suspense fallback={<div className="loading-space" role="status">正在加载页面…</div>}><Routes>
      <Route element={<Shell />}>
        <Route path="/audits" element={<Workbench />} />
        <Route path="/audits/:id/review" element={<Review />} />
        <Route path="/audits/:id/results" element={<Results />} />
      </Route>
      <Route path="*" element={<Navigate to="/audits" replace />} />
    </Routes></Suspense>
  );
}
