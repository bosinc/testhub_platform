import * as curlconverter from 'curlconverter'

export interface RequestModel {
  method: string
  baseURL: string
  path: string
  query: QueryParam[]
  headers: Header[]
  body: Body
  auth?: Auth
  timeout?: number
}

export interface QueryParam {
  key: string
  value: string
  enabled: boolean
}

export interface Header {
  key: string
  value: string
  enabled: boolean
}

export interface Body {
  mode: 'none' | 'raw' | 'json' | 'formdata' | 'urlencoded' | 'binary'
  raw?: string
  json?: string
  formdata?: FormField[]
  urlencoded?: FormField[]
  binary?: File
}

export interface FormField {
  key: string
  value: string
  type: 'text' | 'file'
  enabled: boolean
}

export interface Auth {
  type: 'none' | 'basic' | 'bearer' | 'api_key'
  username?: string
  password?: string
  token?: string
  key?: string
  value?: string
  addTo?: 'header' | 'query'
}

export class RequestModelParser {
  static async parseCurl(curlCommand: string): Promise<RequestModel> {
    try {
      const harData = curlconverter.toHarString(curlCommand)
      const har = JSON.parse(harData)
      
      if (!har.log || !har.log.entries || har.log.entries.length === 0) {
        throw new Error('无法解析CURL命令，请检查格式')
      }
      
      const entry = har.log.entries[0]
      const request = entry.request
      
      // console.log('HAR request URL:', request.url)
      // console.log('HAR request queryString:', request.queryString)
      
      const url = new URL(request.url)
      const baseURL = `${url.protocol}//${url.host}`
      const path = url.pathname
      
      const query: QueryParam[] = []
      
      if (request.queryString && request.queryString.length > 0) {
        request.queryString.forEach((param: any) => {
          query.push({ key: param.name, value: param.value || '', enabled: true })
        })
      } else {
        url.searchParams.forEach((value, key) => {
          query.push({ key, value, enabled: true })
        })
      }
      
      // console.log('Parsed URL:', { baseURL, path, search: url.search, searchParams: Array.from(url.searchParams.entries()), query })
      
      const headers: Header[] = []
      
      if (request.cookies && request.cookies.length > 0) {
        const cookieValue = request.cookies
          .map((cookie: any) => `${cookie.name}=${cookie.value}`)
          .join('; ')
        headers.push({ key: 'Cookie', value: cookieValue, enabled: true })
      }
      
      request.headers.forEach((header: any) => {
        headers.push({ key: header.name, value: header.value, enabled: true })
      })
      
      // console.log('HAR headers:', request.headers)
      // console.log('HAR cookies:', request.cookies)
      // console.log('Parsed headers:', headers)
      
      const body: Body = { mode: 'none' }
      if (request.postData) {
        const postData = request.postData
        const contentType = headers.find(h => h.key.toLowerCase() === 'content-type')?.value || postData.mimeType
        
        if (contentType?.includes('application/json')) {
          body.mode = 'json'
          body.json = postData.text || ''
        } else if (contentType?.includes('application/x-www-form-urlencoded')) {
          body.mode = 'urlencoded'
          body.urlencoded = []
          if (postData.params) {
            postData.params.forEach((param: any) => {
              body.urlencoded?.push({ 
                key: param.name, 
                value: param.value || '', 
                type: 'text', 
                enabled: true 
              })
            })
          } else if (postData.text) {
            new URLSearchParams(postData.text).forEach((value, key) => {
              body.urlencoded?.push({ key, value, type: 'text', enabled: true })
            })
          }
        } else if (contentType?.includes('multipart/form-data')) {
          body.mode = 'formdata'
          body.formdata = []
          if (postData.params) {
            postData.params.forEach((param: any) => {
              body.formdata?.push({
                key: param.name,
                value: param.value || param.fileName || '',
                type: param.fileName ? 'file' : 'text',
                enabled: true
              })
            })
          }
        } else {
          body.mode = 'raw'
          body.raw = postData.text || ''
        }
      }
      
      return {
        method: request.method || 'GET',
        baseURL,
        path,
        query,
        headers,
        body,
        auth: RequestModelParser.parseAuth(headers),
        timeout: 30000
      }
    } catch (error: any) {
      console.error('Failed to parse cURL command:', error)
      const errorMessage = error?.message || error?.toString() || 'Unknown error'
      throw new Error(`cURL命令解析失败: ${errorMessage}，请检查命令格式`)
    }
  }
  
  static parseAuth(headers: Header[]): Auth | undefined {
    const authHeader = headers.find(h => h.key.toLowerCase() === 'authorization')
    if (authHeader) {
      const value = authHeader.value
      
      if (value.startsWith('Bearer ')) {
        return {
          type: 'bearer',
          token: value.substring(7)
        }
      }
      
      if (value.startsWith('Basic ')) {
        try {
          const decoded = atob(value.substring(6))
          const separator = decoded.indexOf(':')
          const username = separator < 0 ? decoded : decoded.slice(0, separator)
          const password = separator < 0 ? '' : decoded.slice(separator + 1)
          return {
            type: 'basic',
            username,
            password
          }
        } catch {
          return undefined
        }
      }
    }
    
    return undefined
  }
  
  static toCurl(model: RequestModel): string {
    const quote = (value: unknown) => `'${String(value).replace(/'/g, `'\\''`)}'`
    const url = new URL(model.baseURL + model.path)
    for (const row of model.query) if (row.enabled !== false && row.key) url.searchParams.append(row.key, String(row.value))
    const auth = model.auth
    if (auth?.type === 'api_key' && auth.addTo === 'query' && auth.key) url.searchParams.append(auth.key, auth.value || '')
    let curl = `curl -X ${quote(model.method)} ${quote(url.toString())}`
    for (const row of model.headers) if (row.enabled !== false && row.key) curl += ` -H ${quote(`${row.key}: ${row.value}`)}`
    if (auth?.type === 'basic') curl += ` -u ${quote(`${auth.username || ''}:${auth.password || ''}`)}`
    if (auth?.type === 'bearer') curl += ` -H ${quote(`Authorization: Bearer ${auth.token || ''}`)}`
    if (auth?.type === 'api_key' && auth.addTo !== 'query' && auth.key) curl += ` -H ${quote(`${auth.key}: ${auth.value || ''}`)}`
    if (model.body.mode === 'json') {
      if (!model.headers.some(row => row.enabled !== false && row.key.toLowerCase() === 'content-type')) curl += ` -H 'Content-Type: application/json'`
      curl += ` --data-raw ${quote(model.body.json || '')}`
    } else if (model.body.mode === 'raw' && model.body.raw) curl += ` --data-raw ${quote(model.body.raw)}`
    else if (model.body.mode === 'urlencoded') {
      for (const row of model.body.urlencoded || []) if (row.enabled !== false && row.key) curl += ` --data-urlencode ${quote(`${row.key}=${row.value}`)}`
    } else if (model.body.mode === 'formdata') {
      for (const row of model.body.formdata || []) {
        if (row.enabled === false || !row.key) continue
        if (row.type === 'file') {
          const file = row.value as unknown as { filename: string; content_type: string }
          // Generated examples reference the selected file name; no local path is persisted.
          curl += ` -F ${quote(`${row.key}=@${file.filename};type=${file.content_type}`)}`
        } else curl += ` --form-string ${quote(`${row.key}=${row.value}`)}`
      }
    } else if (model.body.mode === 'binary' && model.body.binary) {
      curl += ` --data-binary ${quote(`@${(model.body.binary as unknown as { filename: string }).filename}`)}`
    }
    if (model.timeout) curl += ` --max-time ${Number(model.timeout) / 1000}`
    return curl
  }
  
  static toFormData(model: RequestModel): FormData {
    const formData = new FormData()
    
    if (model.body.mode === 'formdata' && model.body.formdata) {
      model.body.formdata.forEach(field => {
        if (field.enabled && field.key) {
          if (field.type === 'file' && field.value) {
            formData.append(field.key, field.value as any)
          } else {
            formData.append(field.key, field.value)
          }
        }
      })
    }
    
    return formData
  }
  
  static toURLSearchParams(model: RequestModel): URLSearchParams {
    const params = new URLSearchParams()
    
    if (model.body.mode === 'urlencoded' && model.body.urlencoded) {
      model.body.urlencoded.forEach(field => {
        if (field.enabled && field.key) {
          params.append(field.key, field.value)
        }
      })
    }
    
    return params
  }
}
