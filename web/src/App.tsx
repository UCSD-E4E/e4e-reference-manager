import { Link, Route, Routes } from "react-router-dom";
import Libraries from "./pages/Libraries";
import LibraryView from "./pages/LibraryView";
import ItemView from "./pages/ItemView";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          e4e&nbsp;References
        </Link>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Libraries />} />
          <Route path="/libraries/:libId" element={<LibraryView />} />
          <Route path="/items/:itemId" element={<ItemView />} />
        </Routes>
      </main>
    </div>
  );
}
