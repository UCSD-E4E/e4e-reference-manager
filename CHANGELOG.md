# Changelog

## [1.20.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.19.0...v1.20.0) (2026-10-30)


### Features

* **web:** type a page number in the PDF viewer ([d5c0318](https://github.com/UCSD-E4E/e4e-reference-manager/commit/d5c0318cee2f0b3a72379665c1a50d1fce19037e))
* **web:** type a page number or book page label in the PDF viewer ([8b4537d](https://github.com/UCSD-E4E/e4e-reference-manager/commit/8b4537d6105587ec2f8b7cdd3b9de3a9bf0281f7))
* **web:** use the PDF's own page labels in the viewer ([db5d3af](https://github.com/UCSD-E4E/e4e-reference-manager/commit/db5d3af3c2695fbfe81813c3dd48379232dd92b7))

## [1.19.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.18.0...v1.19.0) (2026-10-29)


### Features

* **mcp:** accept the API token in an X-API-Key header ([80d8576](https://github.com/UCSD-E4E/e4e-reference-manager/commit/80d857661e9b79a3c295f3e04e73e0ed895c5c33))
* **mcp:** accept the API token in an X-API-Key header ([24c198a](https://github.com/UCSD-E4E/e4e-reference-manager/commit/24c198a470e16508a08f41d4cc844deb7636cc66))

## [1.18.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.17.0...v1.18.0) (2026-10-29)


### Features

* **web:** search text in the PDF viewer ([d78b189](https://github.com/UCSD-E4E/e4e-reference-manager/commit/d78b189f5d6a664c92ffe375c0ebc145fa1e47d5))
* **web:** search text in the PDF viewer ([dd7fa65](https://github.com/UCSD-E4E/e4e-reference-manager/commit/dd7fa6559e18457dc9f65520ff347c68a09558b3))


### Bug Fixes

* **deploy:** raise GROBID's memory cap to 5g ([e82c143](https://github.com/UCSD-E4E/e4e-reference-manager/commit/e82c1439e2b7b536a6c974578a1bdbfa3da29498))
* **deploy:** raise GROBID's memory cap to 5g ([5add741](https://github.com/UCSD-E4E/e4e-reference-manager/commit/5add741c464584c25a633fcd09733cd416ac499a))
* **deploy:** verify both compose store-path forms (krg-infra [#621](https://github.com/UCSD-E4E/e4e-reference-manager/issues/621)) ([7435b24](https://github.com/UCSD-E4E/e4e-reference-manager/commit/7435b249c30c9bff5f9cad5cfd108f579df722d0))
* **deploy:** verify both compose store-path forms (krg-infra [#621](https://github.com/UCSD-E4E/e4e-reference-manager/issues/621)) ([6477f67](https://github.com/UCSD-E4E/e4e-reference-manager/commit/6477f67cab68237168d1cf3422278ff1e50ed89d))

## [1.17.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.16.1...v1.17.0) (2026-10-24)


### Features

* citation graph of a project's papers ([ce7b4e8](https://github.com/UCSD-E4E/e4e-reference-manager/commit/ce7b4e829b36c6eb3309a27f01c47e4939542755))
* citation graph of a project's papers ([ea0fa47](https://github.com/UCSD-E4E/e4e-reference-manager/commit/ea0fa47ba5b06ab0a501a8f06d8ac1d00ec6dee6))

## [1.16.1](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.16.0...v1.16.1) (2026-10-24)


### Bug Fixes

* **deploy:** use GROBID's CPU-only CRF image (32.5 GB → 1.7 GB) ([827cbd1](https://github.com/UCSD-E4E/e4e-reference-manager/commit/827cbd1b617fa7a954fe40eef6f7f9dcd73727a2))
* **deploy:** use GROBID's CPU-only CRF image (32.5 GB → 1.7 GB) ([85478e3](https://github.com/UCSD-E4E/e4e-reference-manager/commit/85478e36ab97977e61eaa933e2182fcabb3281e5))
* export address, ISBN/ISSN, notes and arXiv eprints in BibTeX ([793e21e](https://github.com/UCSD-E4E/e4e-reference-manager/commit/793e21ebffd3c173c6f16f5f0bfc09c808c40765))
* export address, ISBN/ISSN, notes and arXiv eprints in BibTeX ([fd2ffa4](https://github.com/UCSD-E4E/e4e-reference-manager/commit/fd2ffa45535b6165c930869cf18dfee34a119bae))

## [1.16.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.15.4...v1.16.0) (2026-10-24)


### Features

* **web:** copy BibTeX to the clipboard from the Export .bib button ([3b2eebb](https://github.com/UCSD-E4E/e4e-reference-manager/commit/3b2eebba54def4f8e0f5d254ac57f2d94de3b2ba))
* **web:** copy BibTeX to the clipboard from the Export .bib button ([36967b8](https://github.com/UCSD-E4E/e4e-reference-manager/commit/36967b8dd3cdf142d473ce2bf0030bbd5e971d83))

## [1.15.4](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.15.3...v1.15.4) (2026-10-20)


### Bug Fixes

* drop NUL bytes from extracted PDF text so such PDFs can be attached ([843a787](https://github.com/UCSD-E4E/e4e-reference-manager/commit/843a787cf5e0f0492197a2964975883ae6147961))
* drop NUL bytes from extracted PDF text so such PDFs can be attached ([db04b40](https://github.com/UCSD-E4E/e4e-reference-manager/commit/db04b40db1a9fd103cb969fbc1f78c150f605fc5))

## [1.15.3](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.15.2...v1.15.3) (2026-10-20)


### Bug Fixes

* verify DataCite DOIs (arXiv, Zenodo) instead of flagging them not_found ([050b5c9](https://github.com/UCSD-E4E/e4e-reference-manager/commit/050b5c92b654c5bd1e4510b79e0a5f78bb73d4fc))
* verify DataCite DOIs (arXiv, Zenodo) instead of flagging them not_found ([33f98f5](https://github.com/UCSD-E4E/e4e-reference-manager/commit/33f98f57fa67add3c4fe1bd305d9d6475b1ecf78))

## [1.15.2](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.15.1...v1.15.2) (2026-10-20)


### Bug Fixes

* cap uploads, harden .bib import/export, DOI validation and attachment serving ([91e014a](https://github.com/UCSD-E4E/e4e-reference-manager/commit/91e014a8e37302404aaa3fe3ea4192ed5cee617c))
* cap uploads, harden .bib import/export, fix DOI validation and attachment serving ([53797e6](https://github.com/UCSD-E4E/e4e-reference-manager/commit/53797e6a093a798ec0c280338ecd6dbad180da9c))
* close SSRF gaps in PDF fetch and URL ingest; harden OA PDF lookup ([bf62107](https://github.com/UCSD-E4E/e4e-reference-manager/commit/bf6210786ae9481baac1b9049a86fe5137382ee8))
* pin PDF downloads to the validated IP and refuse internal URLs for ingest ([fb3caa3](https://github.com/UCSD-E4E/e4e-reference-manager/commit/fb3caa308dd3c220e776a2ecb4047d589cd92523))
* prefer IPv4 when pinning a PDF download's address ([151e4ac](https://github.com/UCSD-E4E/e4e-reference-manager/commit/151e4acf5c5f703890374a8cd0224a02018fe03b))
* revoke stale access and harden cookie auth ([077a5cc](https://github.com/UCSD-E4E/e4e-reference-manager/commit/077a5cc9c41f3e6dbe2226538f3603f1ab44b9a8))
* stop concurrent and stale item edits from overwriting newer data ([b2aff6f](https://github.com/UCSD-E4E/e4e-reference-manager/commit/b2aff6f687514fd25fe8307ba1a12d45b7f7b315))

## [1.15.1](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.15.0...v1.15.1) (2026-10-20)


### Bug Fixes

* **api:** run the API image as an unprivileged user ([3fcf757](https://github.com/UCSD-E4E/e4e-reference-manager/commit/3fcf757fa6e752208ce4ea2a8537de5137a2ab1d))
* **deploy:** decouple traefik, add healthchecks, log/memory limits, safer backups ([f404d46](https://github.com/UCSD-E4E/e4e-reference-manager/commit/f404d46f48a92d490e00b5e1138589e11f9f2432))
* **deploy:** harden the reference-manager slot after the 2026-10-20 full-disk outage ([2c7e340](https://github.com/UCSD-E4E/e4e-reference-manager/commit/2c7e340977fcb3b7f087cb1eb5ff94c21e0edb4c))
* **deploy:** never prune an image compose.yml names; prune only after a good start ([dcf8c26](https://github.com/UCSD-E4E/e4e-reference-manager/commit/dcf8c260dca1c0c0c02e84a605cf51526e51ec09))
* **deploy:** un-latch a failed compose-stack unit once the slot can start it ([496c5d1](https://github.com/UCSD-E4E/e4e-reference-manager/commit/496c5d134e1efea5c5aec65a151e88282f1d6414))
* keep PDFs when merging and stop deduping different DOIs ([16d7ff4](https://github.com/UCSD-E4E/e4e-reference-manager/commit/16d7ff40577aeef04c55661e3285a7d785c908af))
* keep PDFs when merging and stop deduping different DOIs ([75a42b6](https://github.com/UCSD-E4E/e4e-reference-manager/commit/75a42b6e15ba875db92babbe34fab9cdb1fec821))
* validate input and harden auto-groups, exports, reindex and MCP auth ([8804e7d](https://github.com/UCSD-E4E/e4e-reference-manager/commit/8804e7dc97ca98ed28e8ac56b9bf5ae4ef503aa1))
* validate input and harden auto-groups, exports, reindex and MCP auth ([a12bcca](https://github.com/UCSD-E4E/e4e-reference-manager/commit/a12bcca5b59fa41241bb1fcaa72a0ebde2dc9812))
* **web:** debounce search, drop stale results, surface delete errors ([a639721](https://github.com/UCSD-E4E/e4e-reference-manager/commit/a6397210a9ed3a1c87839fbf76fa80881890a1c6))
* **web:** debounce search, drop stale results, surface delete errors ([be7d682](https://github.com/UCSD-E4E/e4e-reference-manager/commit/be7d6829a4f1606b5f1e20b66a79d712e74f088a))

## [1.15.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.14.0...v1.15.0) (2026-10-20)


### Features

* one-time PDF upload links for agents ([f2e4c73](https://github.com/UCSD-E4E/e4e-reference-manager/commit/f2e4c73ca13a182e3fd068cc9866d21687593e5c))
* one-time PDF upload links for agents ([06bd896](https://github.com/UCSD-E4E/e4e-reference-manager/commit/06bd896231325fe3505ac13b5b4cf330a3046d09))

## [1.14.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.13.0...v1.14.0) (2026-10-20)


### Features

* find PDFs at the reference's own URL ([cbafaef](https://github.com/UCSD-E4E/e4e-reference-manager/commit/cbafaefc7bdd38f988eef9d4bbd5e9c3b9f5f054))
* find PDFs at the reference's own URL ([1cbb10f](https://github.com/UCSD-E4E/e4e-reference-manager/commit/1cbb10f9ea413d3674b4a103e7e24b1ab4d01783))

## [1.13.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.12.0...v1.13.0) (2026-10-20)


### Features

* agents can delete references (to the trash) and restore them ([766a0f2](https://github.com/UCSD-E4E/e4e-reference-manager/commit/766a0f2fadf1fed95c69d9f5c3f7e28839fbbae6))
* agents can delete references (to the trash) and restore them ([80380ba](https://github.com/UCSD-E4E/e4e-reference-manager/commit/80380bad1a46d1951cad837cefb13c7d127655f9))

## [1.12.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.11.2...v1.12.0) (2026-10-20)


### Features

* deleted references go to the project trash for 30 days ([cd02033](https://github.com/UCSD-E4E/e4e-reference-manager/commit/cd0203337fd74bd1368eefaed9c9ff077ac992e6))
* deleted references go to the project trash for 30 days ([31e4b5e](https://github.com/UCSD-E4E/e4e-reference-manager/commit/31e4b5eb26eb37e7b84cf361d2fd1ad480520be7))

## [1.11.2](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.11.1...v1.11.2) (2026-10-20)


### Bug Fixes

* prune superseded release images regardless of age ([a53bff5](https://github.com/UCSD-E4E/e4e-reference-manager/commit/a53bff5d76aa564ec2e3a357de3a09fd8477a4b5))
* prune superseded release images regardless of age ([669d052](https://github.com/UCSD-E4E/e4e-reference-manager/commit/669d0528c1e0c6354d2e06823334bd4172e9e978))

## [1.11.1](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.11.0...v1.11.1) (2026-10-20)


### Bug Fixes

* build our own amd64 translation-server image ([5106fef](https://github.com/UCSD-E4E/e4e-reference-manager/commit/5106fefc2d15e234430225e315ed4f554bdc8e9a))
* build our own amd64 translation-server image ([70ce2d0](https://github.com/UCSD-E4E/e4e-reference-manager/commit/70ce2d0b0040ab2aa22ff7aabc86905cf566ddc7))

## [1.11.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.10.0...v1.11.0) (2026-10-20)


### Features

* update_reference MCP tool; fix stray braces in imported BibTeX titles ([d7bd37e](https://github.com/UCSD-E4E/e4e-reference-manager/commit/d7bd37e4c905d54c7884426791be6297af9923d4))
* update_reference MCP tool; fix stray braces in imported BibTeX titles ([1f035ca](https://github.com/UCSD-E4E/e4e-reference-manager/commit/1f035caf6d5e16603297a1bf6c4291f401e4f89e))

## [1.10.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.9.0...v1.10.0) (2026-10-20)


### Features

* PDF tools for the MCP server ([d2b396e](https://github.com/UCSD-E4E/e4e-reference-manager/commit/d2b396e4837bad72d768241dfed98d930396ae9f))
* PDF tools for the MCP server ([e092b63](https://github.com/UCSD-E4E/e4e-reference-manager/commit/e092b63cd503f857e1f709aabe618a8a411c1825))

## [1.9.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.8.0...v1.9.0) (2026-10-20)


### Features

* MCP server for AI agents at /api/mcp ([c20c7cb](https://github.com/UCSD-E4E/e4e-reference-manager/commit/c20c7cbb56dcf9fa0214fabef002a80a8d81fa4b))
* MCP server for AI agents at /api/mcp ([ef0086e](https://github.com/UCSD-E4E/e4e-reference-manager/commit/ef0086e37f940eb056569d8de7ab358c97110216))


### Bug Fixes

* harden MCP server after review ([baf6e40](https://github.com/UCSD-E4E/e4e-reference-manager/commit/baf6e407f57bc30e2131d61f09cca9f58c48171d))

## [1.8.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.7.1...v1.8.0) (2026-10-20)


### Features

* personal API tokens for scripts and agents ([b382605](https://github.com/UCSD-E4E/e4e-reference-manager/commit/b38260517b6ccf2900bccc325a7836c96e3e6e5f))
* personal API tokens for scripts and agents ([5aac858](https://github.com/UCSD-E4E/e4e-reference-manager/commit/5aac8588376c10d08c323e898072b7264062356d))

## [1.7.1](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.7.0...v1.7.1) (2026-10-20)


### Bug Fixes

* Find PDFs no longer 500s when one item in a full batch fails ([e6f0b6a](https://github.com/UCSD-E4E/e4e-reference-manager/commit/e6f0b6aa4475ed81c6f3ae98e823c560fdacea0c))
* Find PDFs no longer 500s when one item in a full batch fails ([2a29c94](https://github.com/UCSD-E4E/e4e-reference-manager/commit/2a29c94b1126e8579afa0919fd435173a4088833))

## [1.7.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.6.0...v1.7.0) (2026-10-20)


### Features

* find open-access PDFs for every reference in a project ([9258a7b](https://github.com/UCSD-E4E/e4e-reference-manager/commit/9258a7b4f13b666a64eb9f8729bb7fce35acec86))
* find open-access PDFs for every reference in a project ([5acde59](https://github.com/UCSD-E4E/e4e-reference-manager/commit/5acde59be9875a0f42a2087440848cc2a1cae2c8))

## [1.6.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.5.1...v1.6.0) (2026-10-12)


### Features

* edit references; edits reach the exported .bib ([b26003b](https://github.com/UCSD-E4E/e4e-reference-manager/commit/b26003b7cfeaa34b7ae534c843be379123824990))
* edit references; edits reach the exported .bib ([437ca2d](https://github.com/UCSD-E4E/e4e-reference-manager/commit/437ca2d340e11b229615bad6f9c31e150934565b))

## [1.5.1](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.5.0...v1.5.1) (2026-10-08)


### Bug Fixes

* PDF viewer "Could not load this PDF" — serve .mjs as JavaScript ([b2d47f4](https://github.com/UCSD-E4E/e4e-reference-manager/commit/b2d47f48f8a0cd4ed63236c931410d8de90b2ed0))
* PDF viewer "Could not load this PDF" — serve .mjs as JavaScript ([132f73c](https://github.com/UCSD-E4E/e4e-reference-manager/commit/132f73caf4e19609816ff4b4bcde62c369174ef8))

## [1.5.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.4.0...v1.5.0) (2026-10-08)


### Features

* find a paper's open-access PDF online and attach it ([bd4ddb3](https://github.com/UCSD-E4E/e4e-reference-manager/commit/bd4ddb3caceaa9ebbf9658f18df5b9039bcfa786))
* find a paper's open-access PDF online and attach it ([1a0383c](https://github.com/UCSD-E4E/e4e-reference-manager/commit/1a0383cf369c0c181ffcdde3884210dcb7e79aa2))

## [1.4.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.3.0...v1.4.0) (2026-10-07)


### Features

* paste BibTeX — preview with validation, then add chosen entries ([31536a9](https://github.com/UCSD-E4E/e4e-reference-manager/commit/31536a91c3ab80962d90912fc4d9bf9ef199d0eb))
* paste BibTeX — preview with validation, then add chosen entries ([6d647e4](https://github.com/UCSD-E4E/e4e-reference-manager/commit/6d647e4c5ea02caa43166724a57ad9d85a112a88))

## [1.3.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.2.0...v1.3.0) (2026-10-07)


### Features

* topic auto-groups clustered by paper similarity ([ccc132f](https://github.com/UCSD-E4E/e4e-reference-manager/commit/ccc132faec4ab7e6f9b3a4c17fcd58d21dfb9799))
* topic auto-groups clustered by paper similarity ([2ad49ee](https://github.com/UCSD-E4E/e4e-reference-manager/commit/2ad49eeac5cbe210e210467b63c9c80a5948dc51))

## [1.2.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.1.0...v1.2.0) (2026-10-07)


### Features

* de-duplicate .bib imports and merge existing duplicates; ML tags fixes ([403f86c](https://github.com/UCSD-E4E/e4e-reference-manager/commit/403f86cb04341bc1513e5ff9226d56cb854bfbff))
* de-duplicate .bib imports and merge existing duplicates; ML tags fixes ([6dcf30c](https://github.com/UCSD-E4E/e4e-reference-manager/commit/6dcf30c2604446a25849866272350c48d983fbbf))

## [1.1.0](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.0.2...v1.1.0) (2026-10-07)


### Features

* delete projects ([e1a4742](https://github.com/UCSD-E4E/e4e-reference-manager/commit/e1a474258b61aae66644ff33b00bb402bc7e54aa))
* delete projects ([ea4ad0f](https://github.com/UCSD-E4E/e4e-reference-manager/commit/ea4ad0fed2607f3537072c71e23b879404a960db))

## [1.0.2](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.0.1...v1.0.2) (2026-10-07)


### Bug Fixes

* login 500 when the groups claim repeats a group ([db5e0b3](https://github.com/UCSD-E4E/e4e-reference-manager/commit/db5e0b3afcecd05163ba110e9305a81203b8ff58))
* login 500 when the groups claim repeats a group ([8413619](https://github.com/UCSD-E4E/e4e-reference-manager/commit/8413619317f3a471527114dd3d09d79a63562cfb))

## [1.0.1](https://github.com/UCSD-E4E/e4e-reference-manager/compare/v1.0.0...v1.0.1) (2026-10-07)


### Bug Fixes

* send logged-out visitors through login instead of showing a 401 ([6e7b7a2](https://github.com/UCSD-E4E/e4e-reference-manager/commit/6e7b7a2cb2522e5cc44a372fae7c11759cd350ab))
* send logged-out visitors through login instead of showing a 401 ([67fd627](https://github.com/UCSD-E4E/e4e-reference-manager/commit/67fd6271dae1a5a4eef467b7cfe511e23f8c11c6))
* **web:** concurrent 401s join one login redirect; writes keep their data ([b6ef334](https://github.com/UCSD-E4E/e4e-reference-manager/commit/b6ef33466074f70b63cee50b42e81071b9d8308b))

## 1.0.0 (2026-10-07)


### Features

* release with release-please ([6f1db62](https://github.com/UCSD-E4E/e4e-reference-manager/commit/6f1db62df43fbf0708e8e16ccdf79d4acd8b905c))
* release with release-please (same setup as fishsense-lite) ([3570340](https://github.com/UCSD-E4E/e4e-reference-manager/commit/35703406f17822b7ce4bd068eb087aa0c5d26863))
