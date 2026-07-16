import { AppBar } from "@/components/app/app-bar";
import { Container } from "@/components/app/container";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

/** Temporary P7-0 foundation smoke screen - replaced by "My documents" in P7b. */
export default function Home() {
  return (
    <>
      <AppBar />
      <main className="py-[clamp(20px,3vh,36px)]">
        <Container>
          <h1 className="text-page-title font-heading font-semibold text-navy-600">
            Evaluators foundation
          </h1>
          <p className="mt-1 text-muted-foreground">
            P7-0 theme and shell smoke test. Screens land in P7a onward.
          </p>

          <Card className="mt-6 flex flex-wrap items-center gap-3 p-6">
            <Button>Primary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="secondary">Accent</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="destructive">Danger</Button>
          </Card>

          <div className="mt-4 flex flex-wrap gap-2">
            <Badge>Default</Badge>
            <Badge variant="secondary">Secondary</Badge>
            <Badge variant="outline">Outline</Badge>
            <Badge variant="destructive">Danger</Badge>
          </div>
        </Container>
      </main>
    </>
  );
}
