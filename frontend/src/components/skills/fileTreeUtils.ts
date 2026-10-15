import type { FileNode, SkillBundle } from '../../types/skills'

/**
 * Derive a FileNode[] tree from a script skill bundle.
 * Always prepends a synthetic SKILL.md node (content = bundle.skill_md).
 * Then builds a directory tree from bundle.files keys: directories first,
 * then files, both sorted alphabetically within each level.
 */
export function deriveFileTree(bundle: SkillBundle): FileNode[] {
  const root: FileNode[] = []

  // Synthetic SKILL.md is always the first node.
  const skillMdNode: FileNode = { name: 'SKILL.md', path: 'SKILL.md', isDir: false }
  root.push(skillMdNode)

  // Collect all directory segments present in the file paths.
  const dirs = new Map<string, FileNode>()
  const files: FileNode[] = []

  for (const relPath of Object.keys(bundle.files).filter((k) => k !== 'SKILL.md')) {
    const parts = relPath.split('/')
    if (parts.length > 1) {
      // Build intermediate directory nodes.
      for (let depth = 1; depth < parts.length; depth++) {
        const dirPath = parts.slice(0, depth).join('/')
        if (!dirs.has(dirPath)) {
          dirs.set(dirPath, {
            name: parts[depth - 1] ?? '',
            path: dirPath,
            isDir: true,
            children: [],
          })
        }
      }
    }
    files.push({
      name: parts[parts.length - 1] ?? relPath,
      path: relPath,
      isDir: false,
    })
  }

  // Wire directory children: a dir node's children are dirs and files whose parent is this dir.
  // Sort children: dirs first then files, alphabetically within each group.
  const sortNodes = (nodes: FileNode[]): FileNode[] =>
    nodes.slice().sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
      return a.name.localeCompare(b.name)
    })

  // Group files under their immediate parent directory (or root if no parent).
  const rootDirs = new Map<string, FileNode>() // top-level dir nodes
  for (const [dirPath, dirNode] of dirs) {
    const parts = dirPath.split('/')
    if (parts.length === 1) {
      rootDirs.set(dirPath, dirNode)
    } else {
      const parentPath = parts.slice(0, -1).join('/')
      const parent = dirs.get(parentPath)
      if (parent?.children) {
        if (!parent.children.find((c) => c.path === dirPath)) {
          parent.children.push(dirNode)
        }
      }
    }
  }

  for (const fileNode of files) {
    const parts = fileNode.path.split('/')
    if (parts.length === 1) {
      root.push(fileNode)
    } else {
      const parentPath = parts.slice(0, -1).join('/')
      const parent = dirs.get(parentPath)
      if (parent?.children) {
        parent.children.push(fileNode)
      } else {
        // Orphaned — add to root.
        root.push(fileNode)
      }
    }
  }

  // Sort each directory's children.
  for (const dirNode of dirs.values()) {
    if (dirNode.children) {
      dirNode.children = sortNodes(dirNode.children)
    }
  }

  // Add top-level dirs to root, then sort root (SKILL.md stays first via splicing).
  const rootExtras: FileNode[] = []
  for (const dirNode of rootDirs.values()) {
    rootExtras.push(dirNode)
  }
  const sortedExtras = sortNodes(rootExtras.concat(root.slice(1)))
  return [skillMdNode, ...sortedExtras]
}

/** Return the source content for a given file path from a bundle, including SKILL.md. */
export function getBundleFileContent(
  bundle: SkillBundle,
  relativePath: string,
): string | undefined {
  if (relativePath === 'SKILL.md') return bundle.skill_md
  return bundle.files[relativePath]
}

/** Return true if the path should default to "code" mode (non-markdown files). */
export function isCodeOnlyFile(relativePath: string): boolean {
  return !relativePath.endsWith('.md')
}
