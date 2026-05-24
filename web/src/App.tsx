import { Link, Route, Routes } from "react-router-dom";
import Libraries from "./pages/Libraries";
import LibraryView from "./pages/LibraryView";
import ItemView from "./pages/ItemView";
import Groups from "./pages/Groups";
import ShareTarget from "./pages/ShareTarget";

export default function App() {
  return (
    <div className="app">
      <header className="topbar row">
        <Link to="/" className="brand grow">
          e4e&nbsp;References
        </Link>
        <Link to="/groups" className="navlink">
          Groups
        </Link>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Libraries />} />
          <Route path="/groups" element={<Groups />} />
          <Route path="/share-target" element={<ShareTarget />} />
          <Route path="/libraries/:libId" element={<LibraryView />} />
          <Route path="/items/:itemId" element={<ItemView />} />
        </Routes>
      </main>
    </div>
  );
}
