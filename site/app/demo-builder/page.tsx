import DemoBuilder from "./DemoBuilder";

export const metadata = {
  title: "Aurora Demo Builder",
  description:
    "Try a self-contained Aurora layout builder with a generic Home Assistant entity catalog.",
};

export default function DemoBuilderPage() {
  return <DemoBuilder />;
}