import { RequestModelParser, type RequestModel } from './requestModel'
import * as curlconverter from 'curlconverter'

export interface CodeTemplate {
  language: string
  extension: string
  generator: (curlCommand: string) => string
}

export class CodeGenerator {
  private static languageMap: Record<string, string> = {
    javascript: 'javascript',
    python: 'python',
    java: 'java',
    node: 'node',
    curl: 'http',
    php: 'php',
    go: 'go',
    csharp: 'csharp',
    ruby: 'ruby',
    swift: 'swift',
    kotlin: 'kotlin',
    rust: 'rust',
    dart: 'dart',
    objective_c: 'objc',
    powershell: 'powershell',
    matlab: 'matlab',
    r: 'r',
    ansible: 'ansible',
    c: 'c',
    cfml: 'cfml',
    clojure: 'clojure',
    elixir: 'elixir',
    http: 'http',
    httpie: 'httpie',
    julia: 'julia',
    lua: 'lua',
    ocaml: 'ocaml',
    perl: 'perl',
    wget: 'wget'
  }

  private static languageLabels: Record<string, string> = {
    javascript: 'JavaScript',
    python: 'Python',
    java: 'Java',
    node: 'Node.js',
    curl: 'cURL',
    php: 'PHP',
    go: 'Go',
    csharp: 'C#',
    ruby: 'Ruby',
    swift: 'Swift',
    kotlin: 'Kotlin',
    rust: 'Rust',
    dart: 'Dart',
    objective_c: 'Objective-C',
    powershell: 'PowerShell',
    matlab: 'MATLAB',
    r: 'R',
    ansible: 'Ansible',
    c: 'C',
    cfml: 'CFML',
    clojure: 'Clojure',
    elixir: 'Elixir',
    http: 'HTTP',
    httpie: 'HTTPie',
    julia: 'Julia',
    lua: 'Lua',
    ocaml: 'OCaml',
    perl: 'Perl',
    wget: 'Wget'
  }

  private static languageExtensions: Record<string, string> = {
    javascript: 'js',
    python: 'py',
    java: 'java',
    node: 'js',
    curl: 'sh',
    php: 'php',
    go: 'go',
    csharp: 'cs',
    ruby: 'rb',
    swift: 'swift',
    kotlin: 'kt',
    rust: 'rs',
    dart: 'dart',
    objective_c: 'm',
    powershell: 'ps1',
    matlab: 'm',
    r: 'r',
    ansible: 'yml',
    c: 'c',
    cfml: 'cfm',
    clojure: 'clj',
    elixir: 'ex',
    http: 'http',
    httpie: 'http',
    julia: 'jl',
    lua: 'lua',
    ocaml: 'ml',
    perl: 'pl',
    wget: 'sh'
  }

  static async generateCode(model: RequestModel, language: string): Promise<string> {
    const curlCommand = RequestModelParser.toCurl(model)
    
    if (language === 'curl') {
      return curlCommand
    }
    
    const mappedLanguage = this.languageMap[language] || language

    try {
      switch (mappedLanguage) {
        case 'javascript':
          return curlconverter.toJavaScript(curlCommand)
        case 'python':
          return curlconverter.toPython(curlCommand)
        case 'java':
          return curlconverter.toJava(curlCommand)
        case 'node':
          return curlconverter.toNode(curlCommand)
        case 'http':
          return curlconverter.toHTTP(curlCommand)
        case 'php':
          return curlconverter.toPhp(curlCommand)
        case 'go':
          return curlconverter.toGo(curlCommand)
        case 'csharp':
          return curlconverter.toCSharp(curlCommand)
        case 'ruby':
          return curlconverter.toRuby(curlCommand)
        case 'swift':
          return curlconverter.toSwift(curlCommand)
        case 'kotlin':
          return curlconverter.toKotlin(curlCommand)
        case 'rust':
          return curlconverter.toRust(curlCommand)
        case 'dart':
          return curlconverter.toDart(curlCommand)
        case 'objc':
          return curlconverter.toObjectiveC(curlCommand)
        case 'powershell':
          return curlconverter.toPowershellWebRequest(curlCommand)
        case 'matlab':
          return curlconverter.toMATLAB(curlCommand)
        case 'r':
          return curlconverter.toR(curlCommand)
        case 'ansible':
          return curlconverter.toAnsible(curlCommand)
        case 'c':
          return curlconverter.toC(curlCommand)
        case 'cfml':
          return curlconverter.toCFML(curlCommand)
        case 'clojure':
          return curlconverter.toClojure(curlCommand)
        case 'elixir':
          return curlconverter.toElixir(curlCommand)
        case 'httpie':
          return curlconverter.toHttpie(curlCommand)
        case 'julia':
          return curlconverter.toJulia(curlCommand)
        case 'lua':
          return curlconverter.toLua(curlCommand)
        case 'ocaml':
          return curlconverter.toOCaml(curlCommand)
        case 'perl':
          return curlconverter.toPerl(curlCommand)
        case 'wget':
          return curlconverter.toWget(curlCommand)
        default:
          return curlconverter.toPython(curlCommand)
      }
    } catch (error) {
      console.error('Error generating code:', error)
      return `// Error generating code: ${error}`
    }
  }

  static getSupportedLanguages(): string[] {
    return Object.keys(this.languageMap)
  }

  static getLanguageLabel(language: string): string {
    return this.languageLabels[language] || language
  }

  static getLanguageExtension(language: string): string {
    return this.languageExtensions[language] || 'txt'
  }
}
