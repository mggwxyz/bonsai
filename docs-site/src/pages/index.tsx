import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';

import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={styles.hero}>
      <div className="container">
        <Heading as="h1" className={styles.title}>
          {siteConfig.title}
        </Heading>
        <p className={styles.subtitle}>{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link className="button button--primary button--lg" to="/docs/quickstart">
            Quickstart
          </Link>
          <Link className="button button--secondary button--lg" to="/docs/commands">
            Commands
          </Link>
        </div>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout
      title={siteConfig.title}
      description="Bonsai CLI documentation">
      <HomepageHeader />
      <main>
        <section className={styles.quickPanel}>
          <div className="container">
            <Heading as="h2">Start a managed workspace</Heading>
            <pre className={styles.commandBlock}>
              <code>{`brew tap mggwxyz/tap
brew install bonsai
bonsai clone git@github.com:org/my-app.git my-app
bonsai checkout ma-123-implement-auth
bonsai start`}</code>
            </pre>
          </div>
        </section>
      </main>
    </Layout>
  );
}
