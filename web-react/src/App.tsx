import { AnalyzePage } from './pages/AnalyzePage';
import { HomePage } from './pages/HomePage';
import { RepairPage } from './pages/tools/RepairPage';
import { FlattenPage } from './pages/tools/FlattenPage';
import { ExtractTextPage } from './pages/tools/ExtractTextPage';
import { ExtractImagesPage } from './pages/tools/ExtractImagesPage';
import { CropPage } from './pages/tools/CropPage';
import { PageNumbersPage } from './pages/tools/PageNumbersPage';
import { PdfToImagesPage } from './pages/tools/PdfToImagesPage';
import { NupPage } from './pages/tools/NupPage';
import { MergePage } from './pages/tools/MergePage';
import { ImagesToPdfPage } from './pages/tools/ImagesToPdfPage';
import { PdfToWordPage } from './pages/tools/PdfToWordPage';
import { WatermarkPage } from './pages/tools/WatermarkPage';
import { ProtectPage } from './pages/tools/ProtectPage';
import { UnlockPage } from './pages/tools/UnlockPage';
import { RedactPage } from './pages/tools/RedactPage';
import { CompressPage } from './pages/tools/CompressPage';
import { SplitPage } from './pages/tools/SplitPage';
import { PageOpsPage } from './pages/tools/PageOpsPage';
import { ComparePage } from './pages/tools/ComparePage';
import { MetadataPage } from './pages/tools/MetadataPage';
import { TranslatePage } from './pages/tools/TranslatePage';
import { ComponentGallery } from './dev/ComponentGallery';
import { ToastProvider } from './components/shared/Toast';
import { RouterProvider } from './router/Router';
import type { RouteDef } from './router/Router';
import { AppShell } from './shell/AppShell';
import { WorkspaceProvider } from './workspace/WorkspaceContext';

const routes: Record<string, RouteDef> = {
  home: { key: 'home', component: HomePage },
  analyze: { key: 'analyze', component: AnalyzePage },
  compress: { key: 'compress', component: CompressPage },
  merge: { key: 'merge', component: MergePage },
  split: { key: 'split', component: SplitPage },
  pdf_to_images: { key: 'pdf_to_images', component: PdfToImagesPage },
  images_to_pdf: { key: 'images_to_pdf', component: ImagesToPdfPage },
  pdf_to_word: { key: 'pdf_to_word', component: PdfToWordPage },
  translate: { key: 'translate', component: TranslatePage },
  protect: { key: 'protect', component: ProtectPage },
  unlock: { key: 'unlock', component: UnlockPage },
  redact: { key: 'redact', component: RedactPage },
  page_ops: { key: 'page_ops', component: PageOpsPage },
  crop: { key: 'crop', component: CropPage },
  flatten: { key: 'flatten', component: FlattenPage },
  nup: { key: 'nup', component: NupPage },
  watermark: { key: 'watermark', component: WatermarkPage },
  page_numbers: { key: 'page_numbers', component: PageNumbersPage },
  metadata: { key: 'metadata', component: MetadataPage },
  extract_images: { key: 'extract_images', component: ExtractImagesPage },
  extract_text: { key: 'extract_text', component: ExtractTextPage },
  repair: { key: 'repair', component: RepairPage },
  compare: { key: 'compare', component: ComparePage },
  // Dev-only, not in the sidebar (not sourced from the tool registry) —
  // reachable via #/gallery for continued Phase 1 component review.
  gallery: { key: 'gallery', component: ComponentGallery },
};

export function App() {
  return (
    <ToastProvider>
      {/* Above RouterProvider/AppShell (not inside a tool page) so the
          workspace's single working document survives navigating from
          tool to tool — see WorkspaceContext.tsx. Phase 1: Watermark only;
          Phase 2 turns the single document into an array of a few. */}
      <WorkspaceProvider>
        <RouterProvider routes={routes}>
          <AppShell routes={routes} />
        </RouterProvider>
      </WorkspaceProvider>
    </ToastProvider>
  );
}
